"""`RunnerRegistry.get_or_spawn` must cancel the warm-idle countdown
*before* awaiting any kill-in-flight, otherwise the handler self-blocks
for the entire warm-idle window. The ordering is the cancel-before-reuse
invariant captured in `con.warm-idle-cancel-before-reuse`."""
from __future__ import annotations

import asyncio

import pytest

from src.runner_mgmt.registry import RunnerRegistry

pytestmark = pytest.mark.unit


class _Handle:
    def __init__(self) -> None:
        self.runner_id = "rid-1"
        self.session_id = "sess-1"
        self.runner_secret = "secret"
        self.dead = False


class _Runner:
    def __init__(self) -> None:
        self.spawn_count = 0

    async def spawn(self, payload):
        self.spawn_count += 1
        return _Handle()

    async def send_user_message(self, handle, message):  # pragma: no cover
        raise NotImplementedError

    def stream_events(self, handle):  # pragma: no cover
        raise NotImplementedError

    async def terminate(self, handle):
        handle.dead = True

    async def is_alive(self, handle):
        return not handle.dead


class _RecordingWarmIdle:
    """Drop-in fake for `WarmIdleManager` recording call order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def on_run_finished(self) -> None:  # pragma: no cover
        self.calls.append("on_run_finished")

    def on_user_message(self) -> None:
        self.calls.append("on_user_message")

    async def await_kill_in_flight(self) -> None:
        self.calls.append("await_kill_in_flight")

    async def shutdown(self) -> None:  # pragma: no cover
        self.calls.append("shutdown")


class _NoopHeartbeat:
    def start(self) -> None:  # pragma: no cover - no real watcher
        pass

    async def stop(self) -> None:  # pragma: no cover
        pass

    def beat(self) -> None:  # pragma: no cover
        pass


async def test_reuse_calls_on_user_message_before_await_kill_in_flight():
    runner = _Runner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)

    # Spawn once so a warm entry is in the registry.
    entry = await registry.get_or_spawn("sess-1", {})
    # Replace the real `WarmIdleManager` on the existing entry with
    # the recording fake. This is the smallest possible drop-in: the
    # only `warm_idle` methods exercised by `get_or_spawn`'s reuse
    # path are `on_user_message()` and `await_kill_in_flight()`.
    fake = _RecordingWarmIdle()
    entry.warm_idle = fake  # type: ignore[assignment]

    # Force the entry's `is_alive` cache to expire so the next call
    # re-validates against the runner.
    entry._is_alive_cache = None

    reused = await registry.get_or_spawn("sess-1", {})
    assert reused is entry, "warm entry must be reused, not respawned"
    assert runner.spawn_count == 1
    assert fake.calls == ["on_user_message", "await_kill_in_flight"], (
        f"reuse ordering wrong: {fake.calls}; "
        f"expected on_user_message before await_kill_in_flight"
    )
    await registry.shutdown_all()


async def test_reuse_unblocks_when_countdown_is_long():
    """End-to-end behavioural check: with the real `WarmIdleManager`
    and a 300 s warm-idle window, the second `get_or_spawn` must
    return promptly (not block on the countdown task)."""
    runner = _Runner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=300.0)
    entry = await registry.get_or_spawn("sess-2", {})
    entry.warm_idle.on_run_finished()
    # Force is_alive revalidation through the runner.
    entry._is_alive_cache = None

    reused = await asyncio.wait_for(
        registry.get_or_spawn("sess-2", {}), timeout=1.0
    )
    assert reused is entry
    assert runner.spawn_count == 1
    await registry.shutdown_all()
