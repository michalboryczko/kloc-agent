"""SSE wire-frame contract tests.

Two contracts are exercised:

1. `src/api/internal.py:_dispatch_frame` allocates a monotonic `seq`
   from the ExecutionRegistry ring on each frame BEFORE publishing to
   the event bus. All subscribers (SSE consumers, persister) see the
   same seq on the same dict — the single source of truth for cursor
   replay.
2. `src/streaming/sse.py:make_response` prepends an `id: <seq>\\n` line
   to every encoded SSE frame whose source dict carried a seq, giving
   native `EventSource` clients automatic Last-Event-ID tracking, while
   leaving the data line a valid AG-UI event.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []

    async def publish(self, sid: str, rid: str, frame: dict) -> None:
        self.published.append((sid, rid, frame))


def _make_request(bus: _FakeBus) -> object:
    class _State:
        runner_registry = None
        event_bus = bus
        active_run_by_session: dict[str, str] = {}
        pending_pre_run_started: dict[str, list[dict]] = {}

    class _App:
        state = _State()

    class _Request:
        app = _App()

    return _Request()


@pytest.fixture(autouse=True)
def _reset_execution_registry() -> None:
    """The module-level singleton retains state across tests; clear it
    so each test sees a fresh ring."""
    from src.streaming.execution_registry import execution_registry

    execution_registry._executions.clear()
    yield
    execution_registry._executions.clear()


async def test_dispatch_frame_stamps_monotonic_seq_starting_at_zero() -> None:
    from src.api.internal import _dispatch_frame

    bus = _FakeBus()
    req = _make_request(bus)

    await _dispatch_frame(
        req, "s1", {"type": "RUN_STARTED", "runId": "r1"}
    )
    await _dispatch_frame(
        req,
        "s1",
        {"type": "TEXT_MESSAGE_START", "messageId": "m1", "role": "assistant"},
    )
    await _dispatch_frame(
        req,
        "s1",
        {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": "m1",
            "delta": "hello",
        },
    )

    seqs = [frame.get("seq") for _, _, frame in bus.published]
    assert seqs == [0, 1, 2]


async def test_dispatched_frame_seq_matches_execution_ring_entry() -> None:
    """The seq on the published wire dict must equal the seq recorded
    in the ExecutionRegistry ring entry — they share one allocation."""
    from src.api.internal import _dispatch_frame
    from src.streaming.execution_registry import execution_registry

    bus = _FakeBus()
    req = _make_request(bus)

    await _dispatch_frame(
        req, "s1", {"type": "RUN_STARTED", "runId": "r1"}
    )
    await _dispatch_frame(
        req,
        "s1",
        {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": "m1",
            "delta": "x",
        },
    )

    execution = await execution_registry.get("s1", "r1")
    assert execution is not None
    ring_seqs = [entry["seq"] for entry in execution.events]
    wire_seqs = [frame["seq"] for _, _, frame in bus.published]
    assert ring_seqs == wire_seqs == [0, 1]


async def test_replay_from_returns_events_strictly_after_last_event_id() -> None:
    from src.api.internal import _dispatch_frame
    from src.streaming.execution_registry import execution_registry

    bus = _FakeBus()
    req = _make_request(bus)
    for i in range(5):
        if i == 0:
            await _dispatch_frame(
                req, "s1", {"type": "RUN_STARTED", "runId": "r1"}
            )
        else:
            await _dispatch_frame(
                req,
                "s1",
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": "m1",
                    "delta": f"d{i}",
                },
            )

    execution = await execution_registry.get("s1", "r1")
    assert execution is not None
    replayed = execution.replay_from(2)
    replayed_seqs = [entry["seq"] for entry in replayed]
    assert replayed_seqs == [3, 4]


async def test_buffered_pre_run_started_frames_get_seq_on_flush() -> None:
    """Frames arriving before RUN_STARTED are held in
    `pending_pre_run_started` and flushed in order on RUN_STARTED.
    Each flushed frame must carry a seq."""
    from src.api.internal import _dispatch_frame

    bus = _FakeBus()
    req = _make_request(bus)

    await _dispatch_frame(
        req,
        "s1",
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "x"},
    )
    await _dispatch_frame(
        req,
        "s1",
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "y"},
    )
    await _dispatch_frame(
        req, "s1", {"type": "RUN_STARTED", "runId": "r1"}
    )

    seqs = [frame["seq"] for _, _, frame in bus.published]
    types = [frame["type"] for _, _, frame in bus.published]
    assert types == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
    ]
    assert seqs == [0, 1, 2]


# ---------------------------------------------------------------------------
# `make_response` SSE framing
# ---------------------------------------------------------------------------


class _FakeHeaders:
    def get(self, key: str, default: Any = None) -> Any:
        return default


class _FakeRequest:
    headers = _FakeHeaders()


async def _drain(streaming_response: Any) -> bytes:
    body = b""
    async for chunk in streaming_response.body_iterator:
        body += chunk
    return body


async def test_make_response_prepends_id_line_for_dict_with_seq() -> None:
    from src.streaming.sse import make_response

    async def gen():
        yield {
            "type": "RUN_STARTED",
            "threadId": "s1",
            "runId": "r1",
            "seq": 0,
        }
        yield {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": "m1",
            "delta": "hi",
            "seq": 1,
        }

    response = make_response(_FakeRequest(), gen())
    body = await _drain(response)
    text = body.decode("utf-8")

    assert "id: 0\n" in text
    assert "id: 1\n" in text
    assert text.index("id: 0\n") < text.index("id: 1\n")
    blocks = [b for b in text.split("\n\n") if b.strip()]
    for block in blocks:
        assert block.startswith("id: ")
        assert "\ndata: " in block


async def test_make_response_skips_id_when_seq_missing() -> None:
    """Backwards-compatible: frames without seq still encode cleanly,
    just without an id: line. Guards against breakage if any other
    publisher emits unstamped frames."""
    from src.streaming.sse import make_response

    async def gen():
        yield {
            "type": "RUN_STARTED",
            "threadId": "s1",
            "runId": "r1",
        }

    response = make_response(_FakeRequest(), gen())
    body = (await _drain(response)).decode("utf-8")
    assert body.startswith("data: ")
    assert "id: " not in body


async def test_make_response_does_not_leak_seq_into_unknown_event_warning() -> None:
    """An unknown event type is dropped, but the seq lookup must happen
    BEFORE validation so it doesn't crash on a missing model field."""
    from src.streaming.sse import make_response

    async def gen():
        yield {"type": "runner_ready", "seq": 7}

    response = make_response(_FakeRequest(), gen())
    body = (await _drain(response)).decode("utf-8")
    assert body == ""


async def test_id_line_appears_before_data_line() -> None:
    from src.streaming.sse import make_response

    async def gen():
        yield {
            "type": "TEXT_MESSAGE_END",
            "messageId": "m1",
            "seq": 42,
        }

    response = make_response(_FakeRequest(), gen())
    body = (await _drain(response)).decode("utf-8")

    id_idx = body.index("id: 42\n")
    data_idx = body.index("data: ")
    assert id_idx < data_idx
    assert body.endswith("\n\n")
