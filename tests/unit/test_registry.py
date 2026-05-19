"""Unit tests for RunnerRegistry. Reviewer-2 C1 follow-up:
exercise AC15 race + verify get_or_spawn does not deadlock when the
warm-idle kill task is mid-flight."""
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


class SlowFakeRunner:
    """Concrete `Runner` impl that lets tests synchronize on spawn /
    terminate timing. `terminate_delay_s` simulates a slow stop."""

    def __init__(self, terminate_delay_s: float = 0.0) -> None:
        self._terminate_delay = terminate_delay_s
        self.spawn_count = 0
        self.terminate_count = 0
        self.last_handle: FakeHandle | None = None

    async def spawn(self, payload):  # noqa: ARG002 — payload unused
        self.spawn_count += 1
        self.last_handle = FakeHandle()
        return self.last_handle

    async def send_user_message(self, handle, message):  # pragma: no cover
        raise NotImplementedError

    def stream_events(self, handle):  # pragma: no cover
        raise NotImplementedError

    async def terminate(self, handle):
        if self._terminate_delay:
            await asyncio.sleep(self._terminate_delay)
        handle.dead = True
        self.terminate_count += 1

    async def is_alive(self, handle):
        return not handle.dead


async def test_get_or_spawn_reuses_live_container():
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    entry1 = await registry.get_or_spawn("s1", {})
    entry2 = await registry.get_or_spawn("s1", {})
    assert entry1 is entry2
    assert runner.spawn_count == 1
    await registry.shutdown_all()


async def test_get_or_spawn_isolates_sessions():
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    e1 = await registry.get_or_spawn("s1", {})
    e2 = await registry.get_or_spawn("s2", {})
    assert e1 is not e2
    assert runner.spawn_count == 2
    assert e1.handle.runner_id != e2.handle.runner_id
    await registry.shutdown_all()


async def test_ac15_kill_mid_flight_respawns_fresh():
    """AC15: warm-idle kill is mid-flight; new user message arrives;
    `await_kill_in_flight` lets the kill settle; container is dead;
    `get_or_spawn` must respawn fresh -- not reuse the zombie.

    This test ALSO verifies no deadlock occurs: `_on_evict` removes
    the entry under `_lock`, and `get_or_spawn` is concurrently
    awaiting the kill task. If we held `_lock` across that await we'd
    hang forever."""
    runner = SlowFakeRunner(terminate_delay_s=0.05)
    registry = RunnerRegistry(
        runner=runner, warm_idle_s=0.01, heartbeat_timeout_s=60.0
    )
    e1 = await registry.get_or_spawn("s1", {})
    # Trigger the warm-idle timer; it expires in 10ms then runs
    # `terminate` (50ms) before completing.
    e1.warm_idle.on_run_finished()
    await asyncio.sleep(0.02)  # past timer expiry, into terminate

    # While the kill is still running, request a new spawn.
    e2 = await asyncio.wait_for(
        registry.get_or_spawn("s1", {}), timeout=1.0
    )

    assert runner.spawn_count == 2, "expected a fresh spawn after kill"
    assert runner.terminate_count == 1, "expected the original to be killed"
    assert e1.handle is not e2.handle
    await registry.shutdown_all()


async def test_ac14_warm_container_cancelled_kill_reuses():
    """AC14: new user message arrives BEFORE warm-idle expiry; the
    timer is cancelled and the same container handles the follow-up."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(
        runner=runner, warm_idle_s=10.0, heartbeat_timeout_s=60.0
    )
    e1 = await registry.get_or_spawn("s1", {})
    e1.warm_idle.on_run_finished()
    await asyncio.sleep(0.01)  # timer ticking but not expired
    e1.warm_idle.on_user_message()  # cancels the kill timer
    e2 = await registry.get_or_spawn("s1", {})
    assert e1 is e2
    assert runner.spawn_count == 1
    assert runner.terminate_count == 0
    await registry.shutdown_all()


async def test_audit_emit_runner_spawned():
    runner = SlowFakeRunner()
    events: list[tuple[str, dict]] = []

    async def audit_emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    registry = RunnerRegistry(
        runner=runner, warm_idle_s=60.0, audit_emit=audit_emit
    )
    await registry.get_or_spawn("s1", {})
    assert ("runner_spawned",) == tuple(e[0] for e in events)
    assert events[0][1]["session_id"] == "s1"
    await registry.shutdown_all()


async def test_on_heartbeat_frame_resets_watcher():
    """Reviewer-2 C2 / N1: heartbeat-frame ingestion must reset the
    heartbeat watcher. Without `beat()` being called via the
    `on_heartbeat_frame` hook, healthy runners die at 30s -- and
    dev-1's `_dispatch_frame` looks up the method by exactly this name."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(
        runner=runner, warm_idle_s=60.0, heartbeat_timeout_s=0.05
    )
    entry = await registry.get_or_spawn("s1", {})
    # Keep the heartbeat alive by calling on_heartbeat_frame faster
    # than the 50 ms timeout. If the hook is a no-op, the watcher will
    # fire on_crash, terminate the runner, and remove the entry.
    for _ in range(5):
        await asyncio.sleep(0.02)
        await registry.on_heartbeat_frame("s1")
    still_alive = await registry.get("s1")
    assert still_alive is entry
    assert runner.terminate_count == 0
    await registry.shutdown_all()


async def test_on_heartbeat_frame_name_matches_dispatch_lookup():
    """N1 regression guard: dev-1's `_dispatch_frame` uses
    `getattr(registry, "on_heartbeat_frame", None)` and silently no-ops
    if the attribute is missing. Pin the public name so a future rename
    fails this test instead of fluking past CI and breaking AC13/14."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner)
    fn = getattr(registry, "on_heartbeat_frame", None)
    assert callable(fn), (
        "on_heartbeat_frame must be a callable public method on "
        "RunnerRegistry; dev-1's _dispatch_frame looks it up by this name"
    )


async def test_on_crash_emits_tool_call_crashed_for_in_flight():
    """Reviewer-2 I2: when heartbeat times out, every in-flight tool
    call must surface as `tool_call.crashed` (plan §575, AC20)."""
    runner = SlowFakeRunner()
    events: list[tuple[str, dict]] = []

    async def audit_emit(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    registry = RunnerRegistry(
        runner=runner,
        warm_idle_s=60.0,
        heartbeat_timeout_s=0.05,
        audit_emit=audit_emit,
    )
    await registry.get_or_spawn("s1", {})
    await registry.on_tool_call_started("s1", "tc1", "kloc_search")
    await registry.on_tool_call_started("s1", "tc2", "kloc_context")

    # Don't call on_heartbeat — watcher will time out.
    await asyncio.sleep(0.15)

    kinds = [e[0] for e in events]
    assert "runner_heartbeat_lost" in kinds
    crashed = [e for e in events if e[0] == "tool_call.crashed"]
    assert len(crashed) == 2
    tool_ids = sorted(e[1]["tool_call_id"] for e in crashed)
    assert tool_ids == ["tc1", "tc2"]
    await registry.shutdown_all()


async def test_set_runner_requires_audit_emit():
    """Reviewer-2 R1 + team-lead loop-1: refuse to transition from
    day-1 stub mode to real-runner mode without an audit_emit. Silent
    drop of runner_spawned / warm_idle_evicted / heartbeat_lost /
    tool_call.crashed audit rows is unacceptable."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry()  # day-1 stub mode is fine
    with pytest.raises(ValueError, match="audit_emit required"):
        registry.set_runner(runner)


async def test_set_runner_accepts_audit_emit_passed_in_set():
    """`set_runner` can supply audit_emit even if the constructor was
    no-args."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry()

    async def emit(event_type, payload):
        pass

    registry.set_runner(runner, audit_emit=emit)
    e = await registry.get_or_spawn("s1", {})
    assert e is not None
    await registry.shutdown_all()


async def test_set_runner_accepts_audit_emit_from_constructor():
    """`set_runner` can be called with no audit_emit if the constructor
    already provided one."""
    runner = SlowFakeRunner()

    async def emit(event_type, payload):
        pass

    registry = RunnerRegistry(audit_emit=emit)
    registry.set_runner(runner)  # should not raise
    e = await registry.get_or_spawn("s1", {})
    assert e is not None
    await registry.shutdown_all()


async def test_on_tool_call_completed_removes_in_flight():
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    await registry.get_or_spawn("s1", {})
    await registry.on_tool_call_started("s1", "tc1", "kloc_search")
    await registry.on_tool_call_completed("s1", "tc1")
    entry = await registry.get("s1")
    assert entry is not None
    assert entry.in_flight_tool_calls == {}
    await registry.shutdown_all()


# --- get_by_runner_id O(1) reverse index --------------------------------


async def test_get_by_runner_id_finds_spawned_entry():
    """Regression: previously O(n) linear scan over `_entries` under
    `_lock`. Now O(1) via reverse `runner_id -> session_id` index."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    entry = await registry.get_or_spawn("s1", {})
    found = await registry.get_by_runner_id(entry.handle.runner_id)
    assert found is entry
    await registry.shutdown_all()


async def test_get_by_runner_id_returns_none_after_eviction():
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    entry = await registry.get_or_spawn("s1", {})
    rid = entry.handle.runner_id
    await registry._remove_entry("s1")
    assert await registry.get_by_runner_id(rid) is None


async def test_shutdown_all_clears_reverse_index():
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    await registry.get_or_spawn("s1", {})
    await registry.get_or_spawn("s2", {})
    await registry.shutdown_all()
    assert registry._by_runner_id == {}


async def test_stale_heartbeat_watcher_does_not_evict_fresh_runner():
    """Regression: warm-idle eviction must not let the OLD runner's
    stale HeartbeatWatcher fire `_on_crash` and wipe out a freshly
    spawned runner under the same session_id.

    Repro: spawn runner A, warm-idle expires (no on_run_finished
    needed -- we drive it directly), runner A's entry is removed
    but its heartbeat watcher must also be stopped. Spawn runner B
    under the same session_id. Drive A's heartbeat watcher to
    completion synthetically. Registry must still hold runner B's
    entry, and no `runner_heartbeat_lost` audit row must have been
    emitted for runner A.
    """
    events: list[tuple[str, dict]] = []

    async def audit(kind, payload):
        events.append((kind, payload))

    runner = SlowFakeRunner()
    # Heartbeat timeout shorter than the test budget but long enough that
    # the watcher doesn't trip during the eviction itself.
    registry = RunnerRegistry(
        runner=runner,
        warm_idle_s=0.05,
        heartbeat_timeout_s=10.0,
        audit_emit=audit,
    )

    # First spawn (runner A).
    entry_a = await registry.get_or_spawn("s-stale", {})
    rid_a = entry_a.handle.runner_id

    # Trigger warm-idle eviction.
    entry_a.warm_idle.on_run_finished()
    await asyncio.sleep(0.15)  # let the kill task run

    # Eviction observed.
    kinds = [k for k, _ in events]
    assert "runner_warm_idle_evicted" in kinds
    assert await registry.get("s-stale") is None

    # Second spawn (runner B) under the SAME session_id.
    entry_b = await registry.get_or_spawn("s-stale", {})
    assert entry_b.handle.runner_id != rid_a

    # Now drive runner A's stale watcher's _on_crash callback by hand,
    # simulating what would happen if `_on_evict` had failed to stop it
    # and its 30s timeout fired AFTER runner B was already installed.
    # With the fix, this must NOT remove runner B's entry, and must NOT
    # emit `runner_heartbeat_lost`.
    stale_on_crash = entry_a.heartbeat._on_crash  # type: ignore[attr-defined]
    assert stale_on_crash is not None
    await stale_on_crash()

    # Runner B must still be in the registry.
    still_b = await registry.get("s-stale")
    assert still_b is entry_b, (
        "stale watcher from evicted runner A must not evict fresh runner B"
    )
    # No spurious heartbeat-lost.
    assert "runner_heartbeat_lost" not in [k for k, _ in events], (
        f"stale _on_crash must be a no-op; got events={events}"
    )

    await registry.shutdown_all()


async def test_warm_idle_eviction_stops_heartbeat_watcher():
    """The HeartbeatWatcher of the evicted runner must be stopped as
    part of warm-idle eviction so it doesn't keep looping (and
    eventually firing `_on_crash`) against an already-terminated
    handle."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(
        runner=runner,
        warm_idle_s=0.05,
        heartbeat_timeout_s=10.0,
    )
    entry = await registry.get_or_spawn("s-hb-stop", {})
    hb = entry.heartbeat
    entry.warm_idle.on_run_finished()
    await asyncio.sleep(0.15)

    # Watcher task must have exited.
    assert hb._stopped is True, "heartbeat watcher must be stopped on eviction"
    task = hb._task
    assert task is None or task.done(), (
        "heartbeat watcher task must be done after eviction"
    )

    await registry.shutdown_all()
