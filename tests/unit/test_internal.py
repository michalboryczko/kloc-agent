"""Tests for `src/api/internal.py:_dispatch_frame`.

Regression: `_dispatch_frame` previously dropped any non-lifecycle frame
that arrived before the cached `RUN_STARTED`. Under reconnect / replay
this lost frames silently. Fix: buffer pre-RUN_STARTED frames in
`request.app.state.pending_pre_run_started` and flush them when the
matching RUN_STARTED lands. The buffer is bounded by `_PRE_RUN_BUFFER_CAP`.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []

    async def publish(self, sid: str, rid: str, frame: dict) -> None:
        self.published.append((sid, rid, frame))


def _make_request() -> tuple[object, _FakeBus]:
    bus = _FakeBus()

    class _State:
        runner_registry = None
        event_bus = bus
        active_run_by_session: dict[str, str] = {}
        pending_pre_run_started: dict[str, list[dict]] = {}

    class _App:
        state = _State()

    class _Request:
        app = _App()

    return _Request(), bus


async def test_pre_run_started_frames_are_buffered() -> None:
    from src.api.internal import _dispatch_frame

    req, bus = _make_request()
    await _dispatch_frame(req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "x"})
    assert bus.published == []
    assert len(req.app.state.pending_pre_run_started["s1"]) == 1


async def test_run_started_flushes_buffered_frames() -> None:
    from src.api.internal import _dispatch_frame

    req, bus = _make_request()
    await _dispatch_frame(req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "x"})
    await _dispatch_frame(req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "y"})
    await _dispatch_frame(req, "s1", {"type": "RUN_STARTED", "runId": "r1"})

    types_published = [f["type"] for _, _, f in bus.published]
    assert types_published.count("TEXT_MESSAGE_CONTENT") == 2
    assert "RUN_STARTED" in types_published
    assert "s1" not in req.app.state.pending_pre_run_started


async def test_buffer_is_capped() -> None:
    from src.api.internal import _PRE_RUN_BUFFER_CAP, _dispatch_frame

    req, _ = _make_request()
    for i in range(_PRE_RUN_BUFFER_CAP + 5):
        await _dispatch_frame(
            req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": str(i)}
        )
    assert (
        len(req.app.state.pending_pre_run_started["s1"]) == _PRE_RUN_BUFFER_CAP
    )


async def test_post_run_started_intermediate_frame_routes_correctly() -> None:
    from src.api.internal import _dispatch_frame

    req, bus = _make_request()
    await _dispatch_frame(req, "s1", {"type": "RUN_STARTED", "runId": "r1"})
    await _dispatch_frame(req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "z"})
    # Intermediate frame should publish under r1, no buffer involvement.
    assert any(
        sid == "s1" and rid == "r1" and f["type"] == "TEXT_MESSAGE_CONTENT"
        for sid, rid, f in bus.published
    )


async def test_run_started_publishes_before_buffered_orphans() -> None:
    """ISS-01 regression: RUN_STARTED must reach subscribers at index 0,
    BEFORE any previously-orphan-buffered frame is flushed. AG-UI 0.1.18
    requires RUN_STARTED to be the first event a subscriber observes for
    a run; cursor-replay and resume depend on this ordering.
    """
    from src.api.internal import _dispatch_frame

    req, bus = _make_request()
    # Two frames arrive before any RUN_STARTED → buffered as orphans.
    await _dispatch_frame(
        req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "a"}
    )
    await _dispatch_frame(
        req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "b"}
    )
    assert bus.published == []  # nothing emitted yet — still buffering

    await _dispatch_frame(req, "s1", {"type": "RUN_STARTED", "runId": "r1"})

    types_published = [f["type"] for _, _, f in bus.published]
    assert types_published == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
    ]
    # And the deltas preserve arrival order.
    deltas = [f.get("delta") for _, _, f in bus.published if "delta" in f]
    assert deltas == ["a", "b"]
    # Buffer is cleared after flush.
    assert not req.app.state.pending_pre_run_started.get("s1")


async def test_run_finished_for_current_run_clears_active_mapping() -> None:
    """ISS-04 baseline: a RUN_FINISHED whose runId matches the active run
    for the session SHOULD clear the active mapping. CAS guard must not
    over-correct and prevent normal end-of-run cleanup.
    """
    from src.api.internal import _dispatch_frame

    req, _bus = _make_request()
    req.app.state.active_run_by_session = {"s1": "rA"}
    await _dispatch_frame(req, "s1", {"type": "RUN_FINISHED", "runId": "rA"})

    assert "s1" not in req.app.state.active_run_by_session


async def test_run_finished_for_stale_run_does_not_wipe_active_mapping() -> None:
    """ISS-04 (High): a late RUN_FINISHED for run A arriving after run B's
    RUN_STARTED already took over the session must NOT pop the mapping
    for B. Without the CAS guard, the unconditional pop wipes B's
    routing key and any subsequent intermediate B-frame would be
    misrouted as an orphan.
    """
    from src.api.internal import _dispatch_frame

    req, _bus = _make_request()
    req.app.state.active_run_by_session = {"s1": "rB"}
    await _dispatch_frame(req, "s1", {"type": "RUN_FINISHED", "runId": "rA"})

    assert req.app.state.active_run_by_session.get("s1") == "rB"


async def test_run_error_for_stale_run_does_not_wipe_active_mapping() -> None:
    """Same CAS guarantee for RUN_ERROR as for RUN_FINISHED."""
    from src.api.internal import _dispatch_frame

    req, _bus = _make_request()
    req.app.state.active_run_by_session = {"s1": "rB"}
    await _dispatch_frame(req, "s1", {"type": "RUN_ERROR", "runId": "rA"})

    assert req.app.state.active_run_by_session.get("s1") == "rB"


async def test_stale_run_finished_does_not_orphan_subsequent_b_frame() -> None:
    """End-to-end ISS-04 scenario: stale RUN_FINISHED(rA) arrives while
    run B is active on session s1; the following intermediate frame
    (no runId on the wire) must route via active_run_by_session and be
    published under rB, NOT buffered as a (spurious) orphan.
    """
    from src.api.internal import _dispatch_frame

    req, bus = _make_request()
    req.app.state.active_run_by_session = {"s1": "rB"}

    await _dispatch_frame(req, "s1", {"type": "RUN_FINISHED", "runId": "rA"})
    await _dispatch_frame(
        req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}
    )

    # The intermediate frame must publish under rB.
    routed = [
        (sid, rid, f)
        for sid, rid, f in bus.published
        if f.get("type") == "TEXT_MESSAGE_CONTENT"
    ]
    assert routed, "intermediate TEXT_MESSAGE_CONTENT was not published"
    assert routed[0][0] == "s1"
    assert routed[0][1] == "rB"
    # And it must NOT have landed in the orphan buffer.
    assert not req.app.state.pending_pre_run_started.get("s1")
