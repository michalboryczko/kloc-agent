"""RunnerRegistry. Plan task D6.

Per-session lookup `dict[session_id, RunnerHandle]`. `get_or_spawn`
checks for a live container; otherwise spawns via DockerRunner.
Holds per-session `WarmIdleManager` + heartbeat watcher tasks.

Audit-event emitters: `runner_spawned` (here), `runner_warm_idle_evicted`
(via WarmIdleManager.on_evict), `runner_heartbeat_lost` +
`tool_call.crashed` (via HeartbeatWatcher.on_crash). Plan §575-§578.

Concurrency model (reviewer-2 C1 follow-up):
- `_lock` guards the `_entries` dict only.
- We MUST NOT hold `_lock` while awaiting a kill task, because the
  kill's `_on_evict` callback re-acquires `_lock` to remove the entry.
  `get_or_spawn` therefore takes the lock only for short critical
  sections and releases it across awaits on warm-idle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .heartbeat import HeartbeatWatcher
from .warm_idle import WarmIdleManager

if TYPE_CHECKING:
    from .protocol import Runner, RunnerHandle

log = logging.getLogger(__name__)


# Short window for collapsing back-to-back is_alive checks (e.g. registry
# revalidation followed by spawn-lock double-check) onto a single Docker
# daemon round-trip. Long enough to absorb a hot reuse loop, short enough
# that a freshly-killed container is detected on the next call cycle.
_IS_ALIVE_TTL_S = 0.05


@dataclass
class RegistryEntry:
    handle: "RunnerHandle"
    warm_idle: WarmIdleManager
    heartbeat: HeartbeatWatcher
    inbox: asyncio.Queue
    audit_emit: Callable[[str, dict], Awaitable[None]] | None
    in_flight_tool_calls: dict[str, str] = field(default_factory=dict)
    _is_alive_cache: tuple[bool, float] | None = field(
        default=None, repr=False, compare=False
    )

    async def is_alive(self, runner: "Runner") -> bool:
        """TTL-cached wrapper around `runner.is_alive(self.handle)`.

        Repeated calls within `_IS_ALIVE_TTL_S` return the cached value so
        a single hot reuse loop does not fan out to the Docker daemon. A
        miss (no cache, or expired) calls the runner and caches the result.
        """
        now = time.monotonic()
        cached = self._is_alive_cache
        if cached is not None and (now - cached[1]) < _IS_ALIVE_TTL_S:
            return cached[0]
        value = await runner.is_alive(self.handle)
        self._is_alive_cache = (value, now)
        return value


AuditEmitFn = Callable[[str, dict], Awaitable[None]]
"""(`event_type`, `payload`) -> coroutine. Plan §581 single source of
truth in `src/db/models.py:AuditEventType`; dev-1 wires the actual DB
write."""


class RunnerRegistry:
    """One instance, owned by FastAPI lifespan in `src/main.py`."""

    def __init__(
        self,
        runner: "Runner | None" = None,
        warm_idle_s: float = 60.0,
        heartbeat_timeout_s: float = 30.0,
        audit_emit: AuditEmitFn | None = None,
    ) -> None:
        # `runner=None` is the Phase 2.0 day-1 stub mode (issue #10
        # resolution). dev-1's lifespan can call `RunnerRegistry()` to
        # have lifespan import-clean before the real impl is wired.
        # spawn-paths fail loudly until `set_runner(...)` is called.
        self._runner: "Runner | None" = runner
        self._warm_idle_s = warm_idle_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._audit_emit = audit_emit
        self._entries: dict[str, RegistryEntry] = {}
        # Reverse index `runner_id -> session_id`. Maintained in
        # `_install_entry` and `_remove_entry` so `get_by_runner_id`
        # is O(1). Without this every inbound webhook hot-path call
        # serialized on a full registry scan under `_lock`.
        self._by_runner_id: dict[str, str] = {}
        self._lock = asyncio.Lock()
        # Per-session spawn locks: serialize concurrent `get_or_spawn`
        # callers for the same session_id so we never end up with two
        # runner containers for one session. `_lock` guards only the
        # registry-map invariants -- it can't cover the spawn body
        # because the kill task's `_on_evict` callback re-acquires it
        # (would deadlock; see Concurrency model above).
        self._spawn_locks: dict[str, asyncio.Lock] = {}

    def set_runner(
        self,
        runner: "Runner",
        *,
        warm_idle_s: float | None = None,
        heartbeat_timeout_s: float | None = None,
        audit_emit: AuditEmitFn | None = None,
    ) -> None:
        """Wire the real runner after construction. Called from
        `src/main.py` lifespan once dev-1 applies the bundled
        change-request to instantiate DockerRunner.

        Reviewer-2 R1 + team-lead loop-1 directive: refuse to transition
        from stub-mode to real-runner mode without an `audit_emit`. The
        `RunnerRegistry()` no-args day-1 stub remains supported (it lets
        lifespan import-clean before dev-1's repos exist), but the
        moment a real Runner is wired we lock in the audit chain --
        silent drop of `runner_spawned` / `runner_warm_idle_evicted` /
        `runner_heartbeat_lost` / `tool_call.crashed` is unacceptable
        for QA's scenario assertions."""
        if audit_emit is None and self._audit_emit is None:
            raise ValueError(
                "audit_emit required when a real Runner is wired; "
                "RunnerRegistry() day-1 stub mode is for lifespan "
                "import only. Pass audit_emit=<AuditRepo.append bridge>."
            )
        self._runner = runner
        if warm_idle_s is not None:
            self._warm_idle_s = warm_idle_s
        if heartbeat_timeout_s is not None:
            self._heartbeat_timeout_s = heartbeat_timeout_s
        if audit_emit is not None:
            self._audit_emit = audit_emit

    async def shutdown_all(self) -> None:
        async with self._lock:
            entries = list(self._entries.items())
            self._entries.clear()
            self._by_runner_id.clear()
            self._spawn_locks.clear()
        for session_id, entry in entries:
            await entry.heartbeat.stop()
            await entry.warm_idle.shutdown()
            if self._runner is not None:
                try:
                    await self._runner.terminate(entry.handle)
                except Exception:
                    log.exception(
                        "registry.shutdown_terminate_failed",
                        extra={"session_id": session_id},
                    )

    async def _remove_entry(
        self,
        session_id: str,
        *,
        expected_runner_id: str | None = None,
    ) -> RegistryEntry | None:
        """Lock-scoped helper. Used by `_on_evict` / `_on_crash` and by
        get_or_spawn's "container died" branch.

        When `expected_runner_id` is provided, the removal only fires if
        the currently installed entry's handle matches that runner_id.
        This guards against a stale callback (e.g. a HeartbeatWatcher
        belonging to an already-evicted runner) racing in *after* a
        fresh runner has been installed under the same session_id and
        wiping the new entry out from under us. Without this check the
        first heartbeat-frame from the fresh runner finds an empty
        registry slot, no inbox delivery happens, and the SSE client
        gets an empty 200.
        """
        async with self._lock:
            current = self._entries.get(session_id)
            if (
                current is not None
                and expected_runner_id is not None
                and getattr(current.handle, "runner_id", None)
                != expected_runner_id
            ):
                # Stale caller: a fresh entry has replaced the one this
                # callback was bound to. Leave the new entry alone.
                return None
            entry = self._entries.pop(session_id, None)
            if entry is not None:
                rid = getattr(entry.handle, "runner_id", None)
                if rid is not None:
                    self._by_runner_id.pop(rid, None)
            # Drop the per-session spawn lock to keep `_spawn_locks`
            # bounded. Safe under `_lock` because we never hold the
            # spawn lock here -- only the next `get_or_spawn` would
            # acquire it, and that's the moment a fresh lock should
            # be created anyway.
            self._spawn_locks.pop(session_id, None)
            return entry

    async def _get_entry(self, session_id: str) -> RegistryEntry | None:
        async with self._lock:
            return self._entries.get(session_id)

    async def _get_spawn_lock(self, session_id: str) -> asyncio.Lock:
        """Create-on-miss accessor for the per-session spawn lock.
        Guarded by `_lock` only for the dict mutation itself -- the
        returned lock is then acquired OUTSIDE of `_lock` by the
        caller (`get_or_spawn`) so we don't widen `_lock` over the
        spawn body."""
        async with self._lock:
            lock = self._spawn_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._spawn_locks[session_id] = lock
            return lock

    async def get_or_spawn(
        self, session_id: str, hydration_payload: Any
    ) -> RegistryEntry:
        if self._runner is None:
            raise RuntimeError(
                "RunnerRegistry has no Runner wired; call set_runner() "
                "from lifespan before handling requests"
            )

        # AC15 race: a warm-idle kill task might be mid-flight. We must
        # NOT hold `_lock` while awaiting it, because the kill's
        # `_on_evict` callback acquires `_lock` to remove the entry
        # (deadlock otherwise — reviewer-2 C1).
        entry = await self._get_entry(session_id)
        if entry is not None:
            await entry.warm_idle.await_kill_in_flight()
            # After the kill task settles, the entry may already be gone
            # (`_on_evict` removed it). Re-fetch from the registry.
            entry = await self._get_entry(session_id)
            if entry is not None and await entry.is_alive(self._runner):
                return entry
            # Either entry was evicted by the kill task, or the container
            # is dead despite the entry still existing (rare; e.g. crash
            # without heartbeat-loss yet). Drop and respawn.
            if entry is not None:
                await self._remove_entry(session_id)

        # No live container — spawn fresh. Serialize concurrent
        # spawners for this session_id via a per-session spawn lock so
        # only one container is created when N callers race here at
        # once. We acquire this lock OUTSIDE `_lock` to preserve the
        # deadlock-avoidance invariant documented above (kill task's
        # `_on_evict` callback re-acquires `_lock`).
        spawn_lock = await self._get_spawn_lock(session_id)
        async with spawn_lock:
            # Double-check: a concurrent caller may have already
            # installed a live entry while we waited for the lock.
            existing = await self._get_entry(session_id)
            if existing is not None and await existing.is_alive(self._runner):
                return existing

            handle = await self._runner.spawn(hydration_payload)
            new_entry = await self._install_entry(session_id, handle)

        if self._audit_emit:
            await self._audit_emit(
                "runner_spawned",
                {
                    "session_id": session_id,
                    "runner_id": getattr(handle, "runner_id", None),
                },
            )
        return new_entry

    async def _install_entry(
        self, session_id: str, handle: "RunnerHandle"
    ) -> RegistryEntry:
        inbox: asyncio.Queue = asyncio.Queue(maxsize=64)

        # Forward decls — `entry` is filled in after construction so the
        # callbacks can read in_flight_tool_calls off the live record.
        entry_ref: dict[str, RegistryEntry] = {}

        async def _on_evict() -> None:
            # Stop the heartbeat watcher BEFORE removing the registry
            # entry. Otherwise the stale watcher keeps looping on the
            # already-terminated handle and, ~30s later, fires _on_crash
            # which would wipe out whichever entry currently occupies
            # `session_id` -- including a brand-new runner that arrived
            # after the eviction. (The `expected_runner_id` guard in
            # `_remove_entry` is the belt; this stop() is the braces.)
            try:
                await heartbeat.stop()
            except Exception:
                log.exception(
                    "registry.on_evict_heartbeat_stop_failed",
                    extra={"session_id": session_id},
                )
            await self._remove_entry(
                session_id,
                expected_runner_id=getattr(handle, "runner_id", None),
            )
            if self._audit_emit:
                await self._audit_emit(
                    "runner_warm_idle_evicted",
                    {
                        "session_id": session_id,
                        "runner_id": getattr(handle, "runner_id", None),
                    },
                )

        async def _on_crash() -> None:
            entry = entry_ref.get("v")
            in_flight: dict[str, str] = (
                dict(entry.in_flight_tool_calls) if entry is not None else {}
            )
            removed = await self._remove_entry(
                session_id,
                expected_runner_id=getattr(handle, "runner_id", None),
            )
            if removed is None:
                # Stale watcher (its runner has already been replaced
                # in the registry by a fresh spawn). Do not emit a
                # spurious `runner_heartbeat_lost` for a runner that
                # already exited cleanly via warm-idle eviction.
                return
            if self._audit_emit:
                await self._audit_emit(
                    "runner_heartbeat_lost",
                    {
                        "session_id": session_id,
                        "runner_id": getattr(handle, "runner_id", None),
                    },
                )
                # Plan §575 / AC20: a mid-flight tool call when the
                # runner crashes must surface as `tool_call.crashed`.
                for tool_call_id, tool_name in in_flight.items():
                    await self._audit_emit(
                        "tool_call.crashed",
                        {
                            "session_id": session_id,
                            "runner_id": getattr(handle, "runner_id", None),
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                        },
                    )

        warm_idle = WarmIdleManager(
            runner=self._runner,  # type: ignore[arg-type]
            handle=handle,
            warm_idle_s=self._warm_idle_s,
            on_evict=_on_evict,
        )
        heartbeat = HeartbeatWatcher(
            runner=self._runner,  # type: ignore[arg-type]
            handle=handle,
            timeout_s=self._heartbeat_timeout_s,
            on_crash=_on_crash,
        )
        heartbeat.start()

        entry = RegistryEntry(
            handle=handle,
            warm_idle=warm_idle,
            heartbeat=heartbeat,
            inbox=inbox,
            audit_emit=self._audit_emit,
        )
        entry_ref["v"] = entry

        async with self._lock:
            self._entries[session_id] = entry
            rid = getattr(handle, "runner_id", None)
            if rid is not None:
                self._by_runner_id[rid] = session_id
        return entry

    async def get(self, session_id: str) -> RegistryEntry | None:
        return await self._get_entry(session_id)

    async def get_by_runner_id(self, runner_id: str) -> RegistryEntry | None:
        """O(1) reverse lookup via the `runner_id -> session_id` index
        maintained in `_install_entry` / `_remove_entry`. Used by
        `src/api/webhooks.py` to look up the per-runner HMAC secret on
        every inbound webhook -- O(n) here was a per-event hot-path."""
        async with self._lock:
            sid = self._by_runner_id.get(runner_id)
            if sid is None:
                return None
            return self._entries.get(sid)

    async def inbox_get(
        self, session_id: str, timeout_s: float
    ) -> dict | None:
        """Pull the next inbound frame for `session_id`, or None on
        timeout / no entry. Used by the long-poll endpoint at
        `GET /internal/sessions/{sid}/inbox` (Contract B inbound).

        Returns immediately with None if no entry exists for the
        session (runner is gone / never spawned). Otherwise waits up to
        `timeout_s` for the next queued frame. Frames are queued by
        `src/api/stream.py:stream_post` via `entry.inbox.put({...})`
        when a new user message arrives."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return None
        try:
            return await asyncio.wait_for(
                entry.inbox.get(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            return None

    def known_runner_ids(self) -> list[str]:
        return [
            getattr(e.handle, "runner_id", "") for e in self._entries.values()
        ]

    # ----- Event hooks invoked by `src/api/internal.py` and
    # ----- `src/api/webhooks.py` (dev-1's files; cross-stream contract).

    async def on_heartbeat_frame(self, session_id: str) -> None:
        """Reviewer-2 C2 / N1: called by `src/api/internal.py:_dispatch_frame`
        when a `{"type":"heartbeat",...}` JSONL frame arrives. Resets
        the per-session heartbeat-dead timer.

        Name aligns with dev-1's `_dispatch_frame` getattr lookup
        (`getattr(registry, "on_heartbeat_frame", None)`). Without the
        `_frame` suffix the getattr silently returns None and healthy
        runners die at the 30s heartbeat timeout."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.heartbeat.beat()

    async def on_run_finished(self, session_id: str) -> None:
        """Called by `src/api/internal.py` when a `RUN_FINISHED` JSONL
        frame arrives; starts the warm-idle countdown (AC13)."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.warm_idle.on_run_finished()

    async def on_tool_call_started(
        self, session_id: str, tool_call_id: str, tool_name: str
    ) -> None:
        """Called by `src/api/webhooks.py` on `BeforeToolCall`. Records
        the in-flight call so `_on_crash` can emit `tool_call.crashed`
        if the runner dies before AfterToolCall (AC20)."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.in_flight_tool_calls[tool_call_id] = tool_name

    async def on_tool_call_completed(
        self, session_id: str, tool_call_id: str
    ) -> None:
        """Called by `src/api/webhooks.py` on `AfterToolCall`. Removes
        the in-flight record."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.in_flight_tool_calls.pop(tool_call_id, None)
