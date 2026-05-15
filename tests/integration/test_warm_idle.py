"""Integration tests for the warm-idle eviction state machine (Track D, D7).

Drives dev-2's `WarmIdleManager` against the `FakeRunner` from conftest. No
DB, no Docker — pure asyncio. Verifies AC13, AC14, AC15.
"""
from __future__ import annotations

import asyncio

import pytest

from src.runner_mgmt.warm_idle import WarmIdleManager


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_warm_idle_timer_starts_on_run_finished(mock_runner):
    """RUN_FINISHED schedules a kill task; manager has a pending task."""
    payload_stub = {"session_id": "00000000-0000-0000-0000-000000000001"}
    handle = await mock_runner.spawn(payload_stub)

    mgr = WarmIdleManager(mock_runner, handle, warm_idle_s=10.0)
    mgr.on_run_finished()

    assert mgr._task is not None and not mgr._task.done()
    await mgr.shutdown()


@pytest.mark.asyncio
async def test_warm_idle_timer_cancelled_by_new_user_message(mock_runner):
    """on_user_message() within the window cancels the kill task; same container reused."""
    payload_stub = {"session_id": "00000000-0000-0000-0000-000000000002"}
    handle = await mock_runner.spawn(payload_stub)

    mgr = WarmIdleManager(mock_runner, handle, warm_idle_s=10.0)
    mgr.on_run_finished()
    task = mgr._task
    assert task is not None

    mgr.on_user_message()

    await asyncio.sleep(0)
    await mgr.await_kill_in_flight()

    assert task.done()
    assert mock_runner.terminate_calls == [], (
        "terminate must NOT be called when user message arrives within window"
    )

    await mgr.shutdown()


@pytest.mark.asyncio
async def test_warm_idle_expiry_terminates_container(mock_runner):
    """After warm_idle_s elapses, runner.terminate() is invoked exactly once (AC13)."""
    payload_stub = {"session_id": "00000000-0000-0000-0000-000000000003"}
    handle = await mock_runner.spawn(payload_stub)

    evicted = asyncio.Event()

    async def on_evict() -> None:
        evicted.set()

    mgr = WarmIdleManager(
        mock_runner, handle, warm_idle_s=0.05, on_evict=on_evict
    )
    mgr.on_run_finished()

    await asyncio.wait_for(evicted.wait(), timeout=2.0)

    assert mock_runner.terminate_calls == [handle]
    assert evicted.is_set()


@pytest.mark.asyncio
async def test_warm_idle_evict_swallows_terminate_failure(mock_runner):
    """If runner.terminate raises, on_evict still fires (logged + swallowed)."""
    payload_stub = {"session_id": "00000000-0000-0000-0000-000000000004"}
    handle = await mock_runner.spawn(payload_stub)

    async def raising_terminate(_h):
        raise RuntimeError("simulated docker error")

    mock_runner.terminate = raising_terminate  # type: ignore[assignment]

    evicted = asyncio.Event()

    async def on_evict() -> None:
        evicted.set()

    mgr = WarmIdleManager(
        mock_runner, handle, warm_idle_s=0.05, on_evict=on_evict
    )
    mgr.on_run_finished()

    await asyncio.wait_for(evicted.wait(), timeout=2.0)
    assert evicted.is_set()


@pytest.mark.asyncio
async def test_spawn_vs_reuse_race_await_kill_in_flight(mock_runner):
    """AC15: caller can `await await_kill_in_flight()` to drain a mid-flight kill."""
    payload_stub = {"session_id": "00000000-0000-0000-0000-000000000005"}
    handle = await mock_runner.spawn(payload_stub)

    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow_terminate(_h):
        started.set()
        await finish.wait()
        _h._dead = True

    mock_runner.terminate = slow_terminate  # type: ignore[assignment]

    mgr = WarmIdleManager(mock_runner, handle, warm_idle_s=0.01)
    mgr.on_run_finished()

    await asyncio.wait_for(started.wait(), timeout=2.0)
    assert mgr._task is not None and not mgr._task.done()

    finish.set()

    await mgr.await_kill_in_flight()
    assert mgr._task is not None and mgr._task.done()
    assert handle._dead is True


@pytest.mark.asyncio
async def test_await_kill_in_flight_returns_immediately_when_idle(mock_runner):
    """await_kill_in_flight is a no-op when no kill task is scheduled."""
    payload_stub = {"session_id": "00000000-0000-0000-0000-000000000006"}
    handle = await mock_runner.spawn(payload_stub)

    mgr = WarmIdleManager(mock_runner, handle, warm_idle_s=10.0)
    await mgr.await_kill_in_flight()
    assert mgr._task is None
