"""In-process pub/sub used by stream.py to fan AG-UI events from the
runner-event ingestion (`src/api/internal.py`) out to active SSE
generators. One topic per `(session_id, run_id)` pair.

Multiple subscribers per topic are supported (browser reconnect with
overlapping connections during cursor-replay handoff)."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import AsyncIterator

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], set[asyncio.Queue[dict]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, run_id: str, event: dict) -> None:
        async with self._lock:
            queues = list(self._subs.get((session_id, run_id), ()))
        full: list[asyncio.Queue[dict]] = []
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                full.append(q)
        if not full:
            return
        # A subscriber's queue is saturated -- almost always a slow SSE
        # client. Drop it with a sentinel so the SSE generator exits
        # cleanly rather than silently losing the event (which previously
        # caused missing RUN_FINISHED frames under load).
        log.warning(
            "event_bus.queue_full session=%s run=%s slow_subscribers=%d",
            session_id,
            run_id,
            len(full),
        )
        for q in full:
            try:
                q.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass
        async with self._lock:
            bucket = self._subs.get((session_id, run_id))
            if bucket is not None:
                for q in full:
                    bucket.discard(q)
                if not bucket:
                    self._subs.pop((session_id, run_id), None)

    async def register(
        self,
        session_id: str,
        run_id: str,
        *,
        essential: bool = False,
    ) -> asyncio.Queue[dict]:
        """Create + register a queue for `(session_id, run_id)` WITHOUT
        starting to consume. Use this to close the subscribe-after-publish
        race: register the queue first, then trigger the producer, then
        iterate via `consume`. Returns the queue to pass to `consume`.

        `essential=True` allocates a queue without a size cap so the
        slow-subscriber sentinel cannot drop it. Use ONLY for subscribers
        whose failure causes divergence (the persister: DB rows go
        missing if it sentinel-drops mid-run). Best-effort subscribers
        (SSE clients) keep the bounded queue so a stuck client cannot
        OOM the backend.
        """
        if essential:
            # asyncio.Queue with maxsize=0 is unbounded; `put_nowait`
            # will never raise QueueFull on this queue.
            q: asyncio.Queue[dict] = asyncio.Queue()
        else:
            q = asyncio.Queue(maxsize=10_000)
        key = (session_id, run_id)
        async with self._lock:
            self._subs[key].add(q)
        return q

    async def unregister(
        self, session_id: str, run_id: str, queue: asyncio.Queue[dict]
    ) -> None:
        """Discard a previously-registered queue without iterating it.

        Use this on any early-exit path between `register` and `consume`:
        `consume`'s `finally` block only runs once iteration has started,
        so a queue registered but never consumed leaks into `_subs` and
        `publish` keeps `put_nowait`'ing into it until it saturates and
        triggers the slow-subscriber sentinel for unrelated subscribers.
        """
        key = (session_id, run_id)
        async with self._lock:
            bucket = self._subs.get(key)
            if bucket is not None:
                bucket.discard(queue)
                if not bucket:
                    self._subs.pop(key, None)

    async def consume(
        self, session_id: str, run_id: str, queue: asyncio.Queue[dict]
    ) -> AsyncIterator[dict]:
        """Yield events from a queue previously returned by `register`,
        stopping on the sentinel. Cleans up `_subs` in `finally` exactly
        like `subscribe` does."""
        key = (session_id, run_id)
        try:
            while True:
                event = await queue.get()
                if event is _SENTINEL:
                    return
                yield event
        finally:
            async with self._lock:
                bucket = self._subs.get(key)
                if bucket is not None:
                    bucket.discard(queue)
                    if not bucket:
                        self._subs.pop(key, None)

    async def subscribe(
        self,
        session_id: str,
        run_id: str,
        *,
        essential: bool = False,
    ) -> AsyncIterator[dict]:
        q = await self.register(session_id, run_id, essential=essential)
        async for event in self.consume(session_id, run_id, q):
            yield event

    async def close(self, session_id: str, run_id: str) -> None:
        async with self._lock:
            queues = list(self._subs.get((session_id, run_id), ()))
        for q in queues:
            q.put_nowait(_SENTINEL)


_SENTINEL: dict = {"__sentinel__": True}

event_bus = EventBus()
