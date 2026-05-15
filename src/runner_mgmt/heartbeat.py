"""Heartbeat watcher. Plan task D8: per-session task that times out at
`RUNNER_HEARTBEAT_TIMEOUT_S=30` -> terminate container + mark session
`runner_state='crashed'` + write `runner_heartbeat_lost` audit row.
AC20, AC21."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .protocol import Runner, RunnerHandle

log = logging.getLogger(__name__)

OnCrashCallback = Callable[[], Awaitable[None]]


class HeartbeatWatcher:
    def __init__(
        self,
        runner: "Runner",
        handle: "RunnerHandle",
        timeout_s: float,
        on_crash: OnCrashCallback | None = None,
    ) -> None:
        self._runner = runner
        self._handle = handle
        self._timeout_s = timeout_s
        self._on_crash = on_crash
        self._seen = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def beat(self) -> None:
        self._seen.set()

    async def stop(self) -> None:
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, BaseException):
                pass

    async def _loop(self) -> None:
        while not self._stopped:
            self._seen.clear()
            try:
                await asyncio.wait_for(self._seen.wait(), timeout=self._timeout_s)
            except asyncio.TimeoutError:
                log.warning(
                    "heartbeat.lost",
                    extra={
                        "session_id": getattr(self._handle, "session_id", None)
                    },
                )
                try:
                    await self._runner.terminate(self._handle)
                except Exception:
                    log.exception("heartbeat.terminate_failed")
                if self._on_crash is not None:
                    try:
                        await self._on_crash()
                    except Exception:
                        log.exception("heartbeat.on_crash_failed")
                return
            except asyncio.CancelledError:
                return
