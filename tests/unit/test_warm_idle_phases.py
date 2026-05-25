"""Unit tests for the `WarmIdleManager` countdown vs terminating split.

The bug being guarded: `await_kill_in_flight()` used to await
`self._task` whenever a task existed, including during the countdown
phase. Because the countdown is blocked on `self._activity.wait()`,
which is only set by `on_user_message()`, the caller self-blocked for
the full warm-idle window. The fix gates the await on a `_killing`
flag flipped True only after the activity-wait raises `TimeoutError`.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from src.runner_mgmt.warm_idle import WarmIdleManager

pytestmark = pytest.mark.unit


class _Runner:
    def __init__(self, terminate_delay_s: float = 0.0) -> None:
        self._delay = terminate_delay_s
        self.terminated = asyncio.Event()
        self.terminate_started_at: float | None = None

    async def spawn(self, payload):  # pragma: no cover
        raise NotImplementedError

    async def send_user_message(self, handle, message):  # pragma: no cover
        raise NotImplementedError

    def stream_events(self, handle):  # pragma: no cover
        raise NotImplementedError

    async def terminate(self, handle):
        self.terminate_started_at = time.monotonic()
        if self._delay:
            await asyncio.sleep(self._delay)
        self.terminated.set()

    async def is_alive(self, handle):  # pragma: no cover
        return not self.terminated.is_set()


class _Handle:
    session_id = "sess_phase"


async def test_await_kill_in_flight_returns_quickly_during_countdown():
    """Countdown phase: the manager has a pending task, but no kill is
    actually in flight. `await_kill_in_flight()` must return promptly."""
    runner = _Runner()
    manager = WarmIdleManager(
        runner=runner, handle=_Handle(), warm_idle_s=300.0
    )
    manager.on_run_finished()

    start = time.monotonic()
    await manager.await_kill_in_flight()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05, (
        f"await_kill_in_flight blocked for {elapsed * 1000:.1f} ms "
        f"during countdown; expected near-zero"
    )
    assert not runner.terminated.is_set()
    await manager.shutdown()


async def test_await_kill_in_flight_blocks_during_terminate():
    """Terminate phase: a slow terminate is in flight. Calls to
    `await_kill_in_flight()` must block until it finishes."""
    runner = _Runner(terminate_delay_s=1.0)
    manager = WarmIdleManager(
        runner=runner, handle=_Handle(), warm_idle_s=0.05
    )
    manager.on_run_finished()

    # Let the countdown expire and the manager enter the terminate phase.
    deadline = time.monotonic() + 1.0
    while runner.terminate_started_at is None:
        await asyncio.sleep(0.005)
        if time.monotonic() > deadline:
            pytest.fail("terminate never started")

    # Manager is now in the kill phase. `await_kill_in_flight()` must
    # block until terminate finishes.
    await asyncio.sleep(0.1)
    start = time.monotonic()
    await manager.await_kill_in_flight()
    elapsed = time.monotonic() - start

    assert runner.terminated.is_set()
    assert elapsed >= 0.7, (
        f"await_kill_in_flight returned in {elapsed * 1000:.1f} ms "
        f"during terminate; expected to block until terminate "
        f"completed (~900 ms remaining)"
    )


async def test_killing_flag_resets_on_new_countdown():
    """After a kill completes and `on_run_finished` is called again
    (re-arming the countdown for a follow-up turn), `_killing` must be
    False again so the next caller does not block on the prior task."""
    runner = _Runner()
    manager = WarmIdleManager(
        runner=runner, handle=_Handle(), warm_idle_s=0.05
    )
    manager.on_run_finished()
    await asyncio.wait_for(runner.terminated.wait(), timeout=1.0)
    assert manager._killing is True

    manager.on_run_finished()
    assert manager._killing is False

    start = time.monotonic()
    await manager.await_kill_in_flight()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05

    await manager.shutdown()


async def test_on_user_message_during_countdown_releases_await():
    """A new user message during the countdown phase cancels the
    timer; subsequent `await_kill_in_flight()` is immediate (and the
    container is never terminated)."""
    runner = _Runner()
    manager = WarmIdleManager(
        runner=runner, handle=_Handle(), warm_idle_s=1.0
    )
    manager.on_run_finished()
    await asyncio.sleep(0.05)
    manager.on_user_message()

    start = time.monotonic()
    await manager.await_kill_in_flight()
    elapsed = time.monotonic() - start

    assert elapsed < 0.05
    await asyncio.sleep(0.05)
    assert not runner.terminated.is_set()
    await manager.shutdown()
