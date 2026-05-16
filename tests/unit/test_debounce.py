"""Unit tests for TextDeltaDebouncer. Contract A invariant #2:
256-char / 250-ms buffered flush, force-flush on TEXT_MESSAGE_END."""
from __future__ import annotations

import asyncio

import pytest

from src.streaming.debounce import (
    FLUSH_BYTES,
    FLUSH_INTERVAL_S,
    TextDeltaDebouncer,
)

pytestmark = pytest.mark.unit


def _make_debouncer():
    flushed: list[tuple[str, str]] = []
    finalized: list[str] = []

    async def append_delta(message_id: str, delta: str) -> None:
        flushed.append((message_id, delta))

    async def finalize(message_id: str) -> None:
        finalized.append(message_id)

    return TextDeltaDebouncer(append_delta, finalize), flushed, finalized


async def test_flush_after_byte_threshold():
    debouncer, flushed, _ = _make_debouncer()
    big = "a" * FLUSH_BYTES
    await debouncer.on_content("m1", big)
    assert flushed == [("m1", big)]


async def test_flush_after_interval():
    debouncer, flushed, _ = _make_debouncer()
    await debouncer.on_content("m1", "tiny")
    await asyncio.sleep(FLUSH_INTERVAL_S * 1.5)
    assert flushed == [("m1", "tiny")]


async def test_on_end_forces_flush_and_finalize():
    debouncer, flushed, finalized = _make_debouncer()
    await debouncer.on_content("m1", "incomplete")
    await debouncer.on_end("m1")
    assert flushed == [("m1", "incomplete")]
    assert finalized == ["m1"]


async def test_multiple_messages_keep_separate_buffers():
    debouncer, flushed, _ = _make_debouncer()
    await debouncer.on_content("m1", "abc")
    await debouncer.on_content("m2", "xyz")
    await debouncer.on_end("m1")
    await debouncer.on_end("m2")
    # Either order, both messages flushed exactly once.
    assert sorted(flushed) == [("m1", "abc"), ("m2", "xyz")]


async def test_end_without_content_still_finalizes():
    debouncer, flushed, finalized = _make_debouncer()
    await debouncer.on_end("m1")
    assert flushed == []
    assert finalized == ["m1"]


async def test_timer_firing_after_on_end_does_not_raise():
    """Regression: previously `on_end` did `_buffers.pop(...)` THEN
    cancelled the timer. A timer firing in that gap saw the buffer as
    None and could raise. Fix: cancel timer first, then pop.

    We exercise the path by ending the message immediately after a small
    chunk and waiting past the flush interval — if the stale timer task
    still pokes at the buffer the await would raise here."""
    import asyncio

    debouncer, flushed, finalized = _make_debouncer()
    await debouncer.on_content("m1", "abc")
    await debouncer.on_end("m1")
    # Give any stale timer time to expire.
    await asyncio.sleep(FLUSH_INTERVAL_S * 1.5)

    assert flushed == [("m1", "abc")]
    assert finalized == ["m1"]
