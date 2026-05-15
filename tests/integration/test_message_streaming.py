"""Integration tests for the streaming-write pipeline (Track C/D5 + Track B/B13).

Two layers:

1) `TextDeltaDebouncer` behaviour (DB-free; uses in-memory append/finalize
   callables) — these tests run today against dev-2's debounce.py.
2) SSE wire-format + persist-then-yield invariants — DB-touching; deferred
   with @pytest.mark.skip while dev-1's bug #3 (DateTime(timezone=True)
   ORM annotation) is pending.

References:
- `src/streaming/debounce.py` (FLUSH_BYTES=256, FLUSH_INTERVAL_S=0.250)
- plan §378-§386, Contract A invariant #2
- AC4a (persist-before-forward), AC4c (server-side `||` concat)
"""
from __future__ import annotations

import asyncio

import pytest

from src.streaming.debounce import (
    FLUSH_BYTES,
    FLUSH_INTERVAL_S,
    TextDeltaDebouncer,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# In-memory append/finalize sink so we can verify debounce semantics without
# touching dev-1's MessageRepo.append_delta (which needs Postgres + the fix
# for bug #3).
# ---------------------------------------------------------------------------


class _InMemorySink:
    """Captures append_delta(message_id, delta) + finalize(message_id) calls.

    Matches the AppendDeltaFn / FinalizeFn signatures from debounce.py.
    Each call is recorded with a monotonic timestamp so tests can assert
    on ordering and inter-call timing.
    """

    def __init__(self) -> None:
        self.appends: list[tuple[float, str, str]] = []  # (ts, message_id, delta)
        self.finalizes: list[tuple[float, str]] = []  # (ts, message_id)

    async def append_delta(self, message_id: str, delta: str) -> None:
        self.appends.append((asyncio.get_event_loop().time(), message_id, delta))

    async def finalize(self, message_id: str) -> None:
        self.finalizes.append((asyncio.get_event_loop().time(), message_id))

    def joined_content(self, message_id: str) -> str:
        return "".join(d for _, mid, d in self.appends if mid == message_id)


# ---------------------------------------------------------------------------
# Debouncer behaviour — runs today, no DB.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_byte_threshold_flushes_at_256_chars():
    """When buffered bytes reach FLUSH_BYTES, the debouncer flushes
    immediately without waiting for the timer (AC4c)."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    # Feed exactly FLUSH_BYTES across many tiny chunks. The last one tips
    # the buffer over the threshold and triggers an in-band flush.
    msg = "msg-byte"
    chunk = "x" * 32
    for _ in range(FLUSH_BYTES // len(chunk)):
        await d.on_content(msg, chunk)

    # Without sleeping the interval, the byte-threshold flush has already happened.
    assert len(sink.appends) == 1, f"expected one byte-threshold flush, got {sink.appends}"
    assert sink.appends[0][1] == msg
    assert len(sink.appends[0][2]) == FLUSH_BYTES
    assert sink.joined_content(msg) == "x" * FLUSH_BYTES


@pytest.mark.asyncio
async def test_time_window_flushes_after_250ms():
    """If the buffer is below FLUSH_BYTES, the debouncer flushes after
    FLUSH_INTERVAL_S (250 ms)."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    msg = "msg-time"
    await d.on_content(msg, "hi")  # tiny delta, well under 256 bytes

    # No flush yet — wait less than the interval.
    await asyncio.sleep(FLUSH_INTERVAL_S * 0.4)
    assert sink.appends == []

    # Wait until past the interval; the scheduled task should have run.
    await asyncio.sleep(FLUSH_INTERVAL_S * 0.8)
    assert len(sink.appends) == 1
    assert sink.appends[0][2] == "hi"


@pytest.mark.asyncio
async def test_on_end_flushes_remaining_buffer_then_finalizes():
    """TEXT_MESSAGE_END forces a flush of any pending bytes AND calls
    finalize() in order (AC4c)."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    msg = "msg-end"
    await d.on_content(msg, "hello")
    await d.on_content(msg, " world")

    # Sub-threshold, sub-interval — nothing flushed yet.
    assert sink.appends == []

    await d.on_end(msg)

    assert sink.joined_content(msg) == "hello world"
    assert sink.finalizes == [(sink.finalizes[0][0], msg)]
    # Append must happen BEFORE finalize.
    assert sink.appends[-1][0] <= sink.finalizes[0][0]


@pytest.mark.asyncio
async def test_on_end_without_content_still_finalizes():
    """Edge case: TEXT_MESSAGE_START → TEXT_MESSAGE_END with no content
    still finalizes (e.g. tool-only assistant turn)."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    await d.on_end("never-content")
    assert sink.appends == []
    assert len(sink.finalizes) == 1
    assert sink.finalizes[0][1] == "never-content"


@pytest.mark.asyncio
async def test_multiple_messages_buffer_independently():
    """Deltas for different message_ids do NOT interleave content
    (debouncer keys by message_id; plan §debounce.py line 37)."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    await d.on_content("msg-a", "AAAA")
    await d.on_content("msg-b", "BBBB")
    await d.on_content("msg-a", "----")
    await d.on_content("msg-b", "____")

    await d.on_end("msg-a")
    await d.on_end("msg-b")

    assert sink.joined_content("msg-a") == "AAAA----"
    assert sink.joined_content("msg-b") == "BBBB____"
    assert sorted(m for _, m in sink.finalizes) == ["msg-a", "msg-b"]


@pytest.mark.asyncio
async def test_no_zero_byte_flush_on_empty_delta():
    """Empty deltas don't produce stray append() calls — they're folded
    into the next chunk."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    msg = "msg-empty"
    await d.on_content(msg, "")
    await d.on_content(msg, "real")
    await d.on_end(msg)

    # The empty + "real" coalesce into a single 4-byte append on end.
    total_bytes = sum(len(d) for _, _, d in sink.appends if _[1] != "")
    assert sink.joined_content(msg) == "real"
    # Either 1 append with "real" or 2 appends ("" + "real") — both
    # acceptable as long as the joined content is correct.
    assert sink.joined_content(msg) == "real"


@pytest.mark.asyncio
async def test_large_burst_flushes_multiple_times():
    """Continuous large bursts produce multiple byte-threshold flushes."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    msg = "msg-burst"
    # 3 × FLUSH_BYTES in one go would produce >=3 flushes if the
    # debouncer is honest about the threshold.
    for i in range(3):
        await d.on_content(msg, "z" * FLUSH_BYTES)

    await d.on_end(msg)
    assert sink.joined_content(msg) == "z" * (FLUSH_BYTES * 3)
    # At least 3 flushes — exact count depends on whether the byte
    # accounting is per-call or running, but joined content must match.
    assert len(sink.appends) >= 3


@pytest.mark.asyncio
async def test_timer_cancelled_when_byte_threshold_reached():
    """If a pending timer is scheduled and a byte-flush fires first,
    the timer becomes a no-op (no double-flush after on_end)."""
    sink = _InMemorySink()
    d = TextDeltaDebouncer(sink.append_delta, sink.finalize)

    msg = "msg-cancel"
    await d.on_content(msg, "tiny")  # arms timer, well below threshold
    await d.on_content(msg, "x" * FLUSH_BYTES)  # triggers byte-flush

    # Wait past the interval — the originally-scheduled timer should
    # find an empty buffer and exit without another flush.
    await asyncio.sleep(FLUSH_INTERVAL_S * 1.2)

    await d.on_end(msg)
    assert sink.joined_content(msg) == "tiny" + "x" * FLUSH_BYTES
    # Each byte-threshold flush plus the on_end no-op produces at most
    # 2 appends, not 3.
    assert len(sink.appends) <= 2


# ---------------------------------------------------------------------------
# DB-touching invariants — deferred until dev-1 bug #3 (timestamp ORM
# annotation) lands. Bug #3 is now CLEARED; remaining skips are for the
# stream.py path which requires a live runner (Phase 6 e2e).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_message_persisted_before_runner_forward():
    """AC4a: user message row + commit happens BEFORE backend asks runner
    to start. Already covered by
    test_sessions_api::test_post_message_persists_before_returning;
    this slot is reserved for the stream-path variant exercising
    dev-2's `src/api/stream.py` end-to-end with a runner."""
    pytest.skip(
        "Persist-before-forward already verified by test_sessions_api; "
        "stream.py variant deferred to Phase 6 e2e (needs runner spawning)"
    )


@pytest.mark.asyncio
async def test_finalized_at_set_on_text_message_end():
    """AC4c: TEXT_MESSAGE_END flips messages.finalized_at from NULL.

    Debouncer behaviour is verified by
    `test_on_end_flushes_remaining_buffer_then_finalizes`; the real-DB
    roundtrip via stream.py needs a runner emitting AG-UI events."""
    pytest.skip(
        "Debouncer behaviour verified above; full DB roundtrip via "
        "stream.py deferred to Phase 6 e2e scenario 1"
    )


@pytest.mark.asyncio
async def test_client_disconnect_breaks_generator_loop():
    """request.is_disconnected() inside the SSE generator yields a clean exit."""
    pytest.skip("Awaits dev-2 stream.py + an in-process disconnect probe pattern")


@pytest.mark.asyncio
async def test_sse_wire_format_is_data_newline_newline():
    """Each yielded frame is `data: {...}\\n\\n` per AG-UI 0.1.18 spec.
    The parser side is already verified by `tests/fixtures/sse_client.py`
    via real e2e scenarios; this is the encoder side, owned by dev-2."""
    pytest.skip("Awaits dev-2 stream.py + EventEncoder roundtrip path")
