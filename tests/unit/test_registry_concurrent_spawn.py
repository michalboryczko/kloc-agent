"""Concurrent `get_or_spawn` race regression.

Before the per-session spawn lock, two concurrent `get_or_spawn(sid)`
calls could both pass the "no entry" check and call `Runner.spawn(...)`
twice, leaving two runner containers for one session. The fix
introduces `RunnerRegistry._spawn_locks` so the spawn body is
serialized per session_id without widening `_lock` over the await
(which would re-deadlock with the kill task's `_on_evict` callback).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from src.runner_mgmt.registry import RunnerRegistry

pytestmark = pytest.mark.unit


class FakeHandle:
    def __init__(self, runner_id: str | None = None) -> None:
        self.runner_id = runner_id or str(uuid.uuid4())
        self.session_id = ""
        self.dead = False


class RecordingRunner:
    """Minimal `Runner` impl that records spawn call count and yields
    control on spawn so concurrent coroutines actually race."""

    def __init__(self, spawn_delay_s: float = 0.01) -> None:
        self.spawn_calls = 0
        self.handles: list[FakeHandle] = []
        self._spawn_delay = spawn_delay_s

    async def spawn(self, payload):  # noqa: ARG002 — payload unused
        # The sleep guarantees other coroutines that already passed
        # the "no entry" check have a chance to reach the spawn lock
        # before we install the winner's entry. Without it the first
        # caller could complete before the second is even scheduled.
        await asyncio.sleep(self._spawn_delay)
        self.spawn_calls += 1
        h = FakeHandle()
        self.handles.append(h)
        return h

    async def send_user_message(self, handle, message):  # pragma: no cover
        raise NotImplementedError

    def stream_events(self, handle):  # pragma: no cover
        raise NotImplementedError

    async def terminate(self, handle):
        handle.dead = True

    async def is_alive(self, handle):
        return not handle.dead


async def test_concurrent_get_or_spawn_same_session_spawns_once():
    """~20 callers race `get_or_spawn(sid)` on a fresh registry.
    Exactly one `Runner.spawn` call must fire and every caller must
    receive the same `RegistryEntry`."""
    runner = RecordingRunner(spawn_delay_s=0.02)
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)

    sid = "s-race"
    results = await asyncio.gather(
        *(registry.get_or_spawn(sid, {}) for _ in range(20))
    )

    assert runner.spawn_calls == 1, (
        f"expected exactly 1 spawn under concurrent get_or_spawn, "
        f"got {runner.spawn_calls}"
    )
    first = results[0]
    for r in results[1:]:
        assert r is first, "all concurrent callers must share one entry"

    await registry.shutdown_all()


async def test_concurrent_get_or_spawn_different_sessions_are_not_serialized():
    """Sanity: per-session lock must NOT serialize across sessions.
    Two distinct session_ids run concurrently and each gets its own
    spawn -- 2 spawns total, distinct entries."""
    runner = RecordingRunner(spawn_delay_s=0.02)
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)

    e1_task = asyncio.create_task(registry.get_or_spawn("sid-A", {}))
    e2_task = asyncio.create_task(registry.get_or_spawn("sid-B", {}))
    e1, e2 = await asyncio.gather(e1_task, e2_task)

    assert runner.spawn_calls == 2
    assert e1 is not e2
    assert e1.handle.runner_id != e2.handle.runner_id

    await registry.shutdown_all()
