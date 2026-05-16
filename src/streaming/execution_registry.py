"""Decoupled SSE pattern lifted from Repo 2
(chatbot-app/agentcore/src/streaming/execution_registry.py).

Maps `(session_id, run_id)` -> `Execution(events, status, ts)`. Lets the
runner-ingestion endpoint append AG-UI events to a bounded ring while
SSE clients tail from a cursor and resume cleanly across disconnects.
Caps at 10k events per execution; completed executions evict 5 min
after `RUN_FINISHED`/`RUN_ERROR` to give resumers a window."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Literal

ExecutionStatus = Literal["running", "finished", "errored"]

_RING_CAP = 10_000
_TTL_AFTER_DONE_S = 5 * 60


@dataclass
class Execution:
    session_id: str
    run_id: str
    events: Deque[dict] = field(default_factory=lambda: deque(maxlen=_RING_CAP))
    status: ExecutionStatus = "running"
    started_ts: float = field(default_factory=time.monotonic)
    finished_ts: float | None = None
    next_seq: int = 0
    _activity = None  # set in __post_init__

    def __post_init__(self) -> None:
        self._activity = asyncio.Event()

    def append(self, event: dict) -> int:
        seq = self.next_seq
        self.next_seq += 1
        self.events.append({"seq": seq, "event": event})
        kind = event.get("type")
        if kind == "RUN_FINISHED":
            self.status = "finished"
            self.finished_ts = time.monotonic()
        elif kind == "RUN_ERROR":
            self.status = "errored"
            self.finished_ts = time.monotonic()
        self._activity.set()
        self._activity.clear()
        return seq

    async def wait(self) -> None:
        await self._activity.wait()

    def replay_from(self, last_event_id: int | None) -> list[dict]:
        if last_event_id is None:
            return [e for e in self.events]
        return [e for e in self.events if e["seq"] > last_event_id]

    def is_evictable(self) -> bool:
        if self.status == "running":
            return False
        if self.finished_ts is None:
            raise RuntimeError(
                "Execution.is_evictable invariant violated: status is "
                f"{self.status!r} but finished_ts is None"
            )
        return (time.monotonic() - self.finished_ts) > _TTL_AFTER_DONE_S


class ExecutionRegistry:
    def __init__(self) -> None:
        self._executions: dict[tuple[str, str], Execution] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str, run_id: str) -> Execution:
        async with self._lock:
            key = (session_id, run_id)
            ex = self._executions.get(key)
            if ex is None:
                ex = Execution(session_id=session_id, run_id=run_id)
                self._executions[key] = ex
            return ex

    async def get(self, session_id: str, run_id: str) -> Execution | None:
        async with self._lock:
            return self._executions.get((session_id, run_id))

    async def gc(self) -> int:
        async with self._lock:
            victims = [k for k, v in self._executions.items() if v.is_evictable()]
            for k in victims:
                self._executions.pop(k, None)
            return len(victims)


execution_registry = ExecutionRegistry()
