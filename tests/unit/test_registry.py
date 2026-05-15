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


# --- inbox_get (B-INFRA-7a) ------------------------------------------------


async def test_inbox_get_returns_none_when_no_entry():
    """B-INFRA-7a: no spawned runner for the session -> None immediately."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner)
    msg = await registry.inbox_get("unknown-session", timeout_s=0.05)
    assert msg is None


async def test_inbox_get_times_out_when_no_message():
    """Entry exists but no message arrives within timeout -> None."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    await registry.get_or_spawn("s1", {})
    msg = await registry.inbox_get("s1", timeout_s=0.05)
    assert msg is None
    await registry.shutdown_all()


async def test_inbox_get_returns_queued_message():
    """A message put on entry.inbox by stream_post is delivered by
    inbox_get on the next long-poll."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    entry = await registry.get_or_spawn("s1", {})
    payload = {"type": "user_message", "run_id": "r1", "messages": [{"role": "user", "content": "hi"}]}
    await entry.inbox.put(payload)
    msg = await registry.inbox_get("s1", timeout_s=1.0)
    assert msg == payload
    await registry.shutdown_all()


async def test_inbox_get_zero_timeout_returns_immediately():
    """timeout_s=0 must not block — either return immediately with a
    queued message or with None."""
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    await registry.get_or_spawn("s1", {})
    # Empty queue, zero timeout -> None right away.
    msg = await registry.inbox_get("s1", timeout_s=0)
    assert msg is None
    await registry.shutdown_all()
