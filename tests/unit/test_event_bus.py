"""Tests for `src/streaming/event_bus.py`.

Regression: `publish` previously called `q.put_nowait(event)` with no
QueueFull handler, silently dropping the event for any saturated
subscriber. Fix: catch QueueFull, sentinel-close the slow subscriber,
and remove it from the bucket so it stops getting events.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


async def test_full_queue_subscriber_is_closed_with_sentinel() -> None:
    from src.streaming.event_bus import EventBus

    bus = EventBus()
    slow_q: asyncio.Queue[dict] = asyncio.Queue(maxsize=2)
    async with bus._lock:
        bus._subs[("s1", "r1")].add(slow_q)
    # Pre-fill so the next publish hits QueueFull.
    await slow_q.put({"already": "queued1"})
    await slow_q.put({"already": "queued2"})

    await bus.publish("s1", "r1", {"type": "TEXT_MESSAGE_CONTENT"})

    async with bus._lock:
        assert ("s1", "r1") not in bus._subs


async def test_healthy_subscriber_still_receives_after_slow_one_drops() -> None:
    from src.streaming.event_bus import EventBus

    bus = EventBus()
    slow_q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    fast_q: asyncio.Queue[dict] = asyncio.Queue(maxsize=100)
    async with bus._lock:
        bus._subs[("s1", "r1")].update({slow_q, fast_q})
    await slow_q.put({"prefilled": True})

    await bus.publish("s1", "r1", {"type": "RUN_FINISHED"})

    assert fast_q.qsize() == 1


async def test_publish_with_no_subscribers_is_noop() -> None:
    from src.streaming.event_bus import EventBus

    bus = EventBus()
    await bus.publish("s1", "r1", {"type": "RUN_STARTED"})
    # No assertion needed beyond "did not raise".


async def test_register_then_publish_then_consume_delivers_event() -> None:
    """Pins the subscribe-after-publish race fix in `stream_post`: when a
    warm runner emits events the instant the inbox is fed, the SSE
    generator hasn't yet entered `event_bus.subscribe` and the queue does
    not exist. `register` + `consume` lets the caller register the queue
    BEFORE triggering the producer, so even an event published before
    consumption starts is delivered."""
    from src.streaming.event_bus import EventBus

    bus = EventBus()

    # Step 1: register the queue (pre-publish). This is what stream.py now
    # does before `inbox.put`.
    queue = await bus.register("s1", "r1")

    # Step 2: publish BEFORE anyone starts iterating `consume`. With the
    # old `subscribe`-only API this event would be dropped.
    await bus.publish("s1", "r1", {"type": "RUN_STARTED"})

    # Step 3: now consume. The earlier event must be delivered.
    agen = bus.consume("s1", "r1", queue)
    try:
        first = await asyncio.wait_for(agen.__anext__(), timeout=1.0)
    finally:
        await agen.aclose()

    assert first == {"type": "RUN_STARTED"}
    # And cleanup ran in `consume`'s finally after aclose().
    async with bus._lock:
        assert ("s1", "r1") not in bus._subs
