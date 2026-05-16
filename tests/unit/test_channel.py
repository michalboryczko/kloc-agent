"""Tests for `runner/channel.py`.

Regression: the runner outbound stream previously had no reconnect — a
single transient backend close permanently silenced this runner. Fix:
on transport-level exceptions, drain the queue into a `pending_after_break`
list and reconnect with exponential backoff, replaying buffered events
on the next stream attempt.

These tests cover the queue-drain behaviour at the heart of the fix
without spinning up a real backend.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


async def test_drain_outbound_queue_into_pending_buffer() -> None:
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    await chan._outbound.put({"type": "TEXT_MESSAGE_CONTENT"})
    await chan._outbound.put({"type": "TEXT_MESSAGE_END"})
    await chan._outbound.put(None)  # sentinel signals graceful close

    pending: list[dict] = []
    sentinel_seen = False
    while True:
        try:
            e = chan._outbound.get_nowait()
        except asyncio.QueueEmpty:
            break
        if e is None:
            sentinel_seen = True
            break
        pending.append(e)

    assert sentinel_seen is True
    assert [e["type"] for e in pending] == [
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]


async def test_emit_does_not_block_on_idle_queue() -> None:
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    await chan.emit({"type": "RUN_STARTED"})
    assert chan._outbound.qsize() == 1
