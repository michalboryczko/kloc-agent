"""Unit tests for WarmIdleManager. Plan task D19.

Verifies:
  - Timer expiry triggers terminate + on_evict.
  - on_user_message cancels the kill timer (AC14).
  - AC15 race: kill-in-flight + new message must await the kill task
    before reuse decision.
"""
from __future__ import annotations

import asyncio

import pytest

from src.runner_mgmt.warm_idle import WarmIdleManager

pytestmark = pytest.mark.unit


class FakeRunner:
    def __init__(self, terminate_delay_s: float = 0.0) -> None:
        self.terminated = asyncio.Event()
        self._delay = terminate_delay_s

    async def spawn(self, payload):
        raise NotImplementedError

    async def send_user_message(self, handle, message):
        raise NotImplementedError

    def stream_events(self, handle):
        raise NotImplementedError

    async def terminate(self, handle):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.terminated.set()

    async def is_alive(self, handle):
        return not self.terminated.is_set()


class FakeHandle:
    session_id = "sess_x"


async def test_warm_idle_expires_and_calls_terminate():
    runner = FakeRunner()
    on_evict = asyncio.Event()

    async def _on_evict():
        on_evict.set()

    manager = WarmIdleManager(
        runner=runner,
        handle=FakeHandle(),
        warm_idle_s=0.05,
        on_evict=_on_evict,
    )
    manager.on_run_finished()
    await asyncio.wait_for(runner.terminated.wait(), timeout=1.0)
    await asyncio.wait_for(on_evict.wait(), timeout=1.0)


async def test_on_user_message_cancels_timer():
    runner = FakeRunner()
    manager = WarmIdleManager(
        runner=runner,
        handle=FakeHandle(),
        warm_idle_s=0.5,
    )
    manager.on_run_finished()
    await asyncio.sleep(0.05)
    manager.on_user_message()
    # Give the would-be expiry plenty of time to NOT fire.
    await asyncio.sleep(0.6)
    assert not runner.terminated.is_set()


async def test_ac15_race_await_kill_in_flight():
    """Kill task is mid-flight (terminate has a small delay); new user
    message arrives. The caller must await `await_kill_in_flight()`
    before deciding spawn-vs-reuse, otherwise reuse-on-zombie occurs."""
    runner = FakeRunner(terminate_delay_s=0.1)
    manager = WarmIdleManager(
        runner=runner,
        handle=FakeHandle(),
        warm_idle_s=0.01,
    )
    manager.on_run_finished()
    # Let the timer fire and terminate task start.
    await asyncio.sleep(0.02)
    await manager.await_kill_in_flight()
    assert runner.terminated.is_set()
