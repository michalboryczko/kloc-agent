"""In-process pub/sub used by stream.py to fan AG-UI events from the
runner-event ingestion (`src/api/internal.py`) out to active SSE
generators. One topic per `(session_id, run_id)` pair.

Multiple subscribers per topic are supported (browser reconnect with
overlapping connections during cursor-replay handoff)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import AsyncIterator


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], set[asyncio.Queue[dict]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, run_id: str, event: dict) -> None:
        async with self._lock:
            queues = list(self._subs.get((session_id, run_id), ()))
        for q in queues:
            q.put_nowait(event)

    async def subscribe(
        self, session_id: str, run_id: str
    ) -> AsyncIterator[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        key = (session_id, run_id)
        async with self._lock:
            self._subs[key].add(q)
        try:
            while True:
                event = await q.get()
                if event is _SENTINEL:
                    return
                yield event
        finally:
            async with self._lock:
                self._subs[key].discard(q)
                if not self._subs[key]:
                    self._subs.pop(key, None)

    async def close(self, session_id: str, run_id: str) -> None:
        async with self._lock:
            queues = list(self._subs.get((session_id, run_id), ()))
        for q in queues:
            q.put_nowait(_SENTINEL)


_SENTINEL: dict = {"__sentinel__": True}

event_bus = EventBus()
