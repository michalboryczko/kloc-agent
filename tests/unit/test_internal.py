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
