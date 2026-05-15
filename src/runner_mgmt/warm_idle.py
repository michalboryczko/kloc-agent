"""Warm-idle eviction per architecture.md §3.3.

One `WarmIdleManager` per running container, owned by `RunnerRegistry`.
Per-session `asyncio.Event`-driven Task (NOT a polling sweeper). Plan
tasks D7 (AC13, AC14, AC15)."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from .protocol import Runner, RunnerHandle

log = logging.getLogger(__name__)

OnEvictCallback = Callable[[], Awaitable[None]]


class WarmIdleManager:
    def __init__(
        self,
        runner: "Runner",
        handle: "RunnerHandle",
        warm_idle_s: float,
        on_evict: OnEvictCallback | None = None,
    ) -> None:
        self._runner = runner
        self._handle = handle
        self._warm_idle_s = warm_idle_s
        self._on_evict = on_evict
        self._activity = asyncio.Event()
        self._task: asyncio.Task | None = None

    def on_run_finished(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._activity.clear()
        self._task = asyncio.create_task(self._await_idle_then_kill())

    def on_user_message(self) -> None:
        self._activity.set()
        if self._task and not self._task.done():
            self._task.cancel()

    async def await_kill_in_flight(self) -> None:
        """AC15 race handling. If a warm-idle kill is mid-flight when a new
        user message arrives, the caller must `await` this before deciding
        spawn-vs-reuse. Returns immediately if no kill is in progress."""
        task = self._task
        if task is None or task.done():
            return
        try:
            await task
        except asyncio.CancelledError:
            return

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, BaseException):
                pass

    async def _await_idle_then_kill(self) -> None:
        try:
            await asyncio.wait_for(
                self._activity.wait(), timeout=self._warm_idle_s
            )
        except asyncio.TimeoutError:
            log.info(
                "warm_idle.evicting",
                extra={"session_id": getattr(self._handle, "session_id", None)},
            )
            try:
                await self._runner.terminate(self._handle)
            except Exception:
                log.exception("warm_idle.terminate_failed")
            if self._on_evict is not None:
                try:
                    await self._on_evict()
                except Exception:
                    log.exception("warm_idle.on_evict_failed")
        except asyncio.CancelledError:
            return
