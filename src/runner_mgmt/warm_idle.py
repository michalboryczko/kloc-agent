"""Warm-idle eviction.

One `WarmIdleManager` per running container, owned by `RunnerRegistry`.
Per-session `asyncio.Event`-driven Task; not a polling sweeper.
"""

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
        # Gates `await_kill_in_flight`: countdown-phase awaiters return
        # immediately, terminate-phase awaiters block until terminate
        # finishes. Flips True only after the activity-wait raises
        # TimeoutError and before `terminate(handle)` is invoked.
        self._killing: bool = False

    def on_run_finished(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._activity.clear()
        self._killing = False
        self._task = asyncio.create_task(self._await_idle_then_kill())

    def on_user_message(self) -> None:
        self._activity.set()
        if self._killing:
            # Terminate is already running; cancelling now would leave
            # the container in a half-killed state and the registry
            # entry stale. Let the kill complete; the caller's
            # `await_kill_in_flight()` then settles cleanly and a
            # fresh spawn replaces the zombie.
            return
        if self._task and not self._task.done():
            self._task.cancel()

    async def await_kill_in_flight(self) -> None:
        """Block only when a terminate is actually in flight.

        During the countdown phase a new user message simply cancels
        the timer; there is no kill to wait on. Awaiting `self._task`
        in that phase would self-block for the whole warm-idle window
        because the countdown only completes via the activity event,
        which the caller has not yet set.
        """
        if not self._killing:
            return
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
            self._killing = True
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
