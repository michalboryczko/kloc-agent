"""Buffered text-delta persistence. Plan §378-§386 + Contract A invariant
#2: every `TEXT_MESSAGE_CONTENT` flows through here, and we flush to
`MessageRepo.append_delta()` either when the per-message buffer reaches
256 chars or 250 ms has elapsed since the first byte arrived. We always
flush on `TEXT_MESSAGE_END` and mark `messages.finalized_at = now()`.

`append_delta(session, message_id, delta_str)` is owned by dev-1
(Contract A cross-stream import, plan §318). We import lazily so the
module loads cleanly during dev-1's parallel work even if `repos`
hasn't been shipped yet."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

FLUSH_BYTES = 256
FLUSH_INTERVAL_S = 0.250


AppendDeltaFn = Callable[[str, str], Awaitable[None]]
FinalizeFn = Callable[[str], Awaitable[None]]


@dataclass
class _MsgBuf:
    buffer: list[str] = field(default_factory=list)
    byte_size: int = 0
    first_ts: float | None = None
    flush_task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TextDeltaDebouncer:
    """Per-session debouncer. Keyed by `message_id` because deltas for
    different assistant messages interleave across tool-call boundaries."""

    def __init__(
        self,
        append_delta: AppendDeltaFn,
        finalize: FinalizeFn,
    ) -> None:
        self._append_delta = append_delta
        self._finalize = finalize
        self._buffers: dict[str, _MsgBuf] = {}

    async def on_content(self, message_id: str, delta: str) -> None:
        buf = self._buffers.setdefault(message_id, _MsgBuf())
        async with buf.lock:
            if buf.first_ts is None:
                buf.first_ts = time.monotonic()
            buf.buffer.append(delta)
            buf.byte_size += len(delta)
            if buf.byte_size >= FLUSH_BYTES:
                await self._flush_locked(message_id, buf)
                return
            if buf.flush_task is None or buf.flush_task.done():
                buf.flush_task = asyncio.create_task(
                    self._flush_after_interval(message_id)
                )

    async def on_end(self, message_id: str) -> None:
        buf = self._buffers.pop(message_id, None)
        if buf is None:
            await self._finalize(message_id)
            return
        async with buf.lock:
            if buf.flush_task is not None and not buf.flush_task.done():
                buf.flush_task.cancel()
            if buf.buffer:
                await self._flush_locked(message_id, buf)
        await self._finalize(message_id)

    async def _flush_after_interval(self, message_id: str) -> None:
        try:
            await asyncio.sleep(FLUSH_INTERVAL_S)
        except asyncio.CancelledError:
            return
        buf = self._buffers.get(message_id)
        if buf is None:
            return
        async with buf.lock:
            if buf.buffer:
                await self._flush_locked(message_id, buf)

    async def _flush_locked(self, message_id: str, buf: _MsgBuf) -> None:
        delta = "".join(buf.buffer)
        buf.buffer.clear()
        buf.byte_size = 0
        buf.first_ts = None
        await self._append_delta(message_id, delta)
