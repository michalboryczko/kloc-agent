"""RunnerRegistry.

Per-session lookup `dict[session_id, RunnerHandle]`. `get_or_spawn`
checks for a live container; otherwise spawns via DockerRunner. Holds
per-session `WarmIdleManager` + heartbeat watcher tasks.

Audit-event emitters: `runner_spawned` (here), `runner_warm_idle_evicted`
(via WarmIdleManager.on_evict), `runner_heartbeat_lost` +
`tool_call.crashed` (via HeartbeatWatcher.on_crash).

Concurrency invariant: `_lock` guards `_entries` only. Holding it across
a kill-task await would deadlock because the kill's `_on_evict` callback
re-acquires it.
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
    # In-flight tool calls: tool_call_id -> tool_name. Populated on
    # BeforeToolCall webhook receipt; cleared on AfterToolCall. Used by
    # `_on_crash` to emit `tool_call.crashed` when the runner dies with
    # tool calls outstanding.
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
"""(`event_type`, `payload`) -> coroutine. Canonical event-type vocabulary
lives in `src/db/models.py:AuditEventType`."""


class RunnerRegistry:
    """One instance, owned by FastAPI lifespan in `src/main.py`."""

    def __init__(
        self,
        runner: "Runner | None" = None,
        warm_idle_s: float = 60.0,
        heartbeat_timeout_s: float = 30.0,
        audit_emit: AuditEmitFn | None = None,
    ) -> None:
        # `runner=None` lets lifespan import-clean before a concrete
        # Runner is wired. Spawn paths fail loudly until `set_runner` runs.
        self._runner: "Runner | None" = runner
        self._warm_idle_s = warm_idle_s
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._audit_emit = audit_emit
        self._entries: dict[str, RegistryEntry] = {}
        # Reverse index runner_id -> session_id, kept O(1) for the
        # inbound-webhook hot path.
        self._by_runner_id: dict[str, str] = {}
        self._lock = asyncio.Lock()
        # Per-session spawn locks serialize concurrent `get_or_spawn`
        # callers for the same session so we never end up with two
        # containers for one session. Held outside `_lock` so the kill
        # task's `_on_evict` callback can re-acquire `_lock` without
        # deadlocking.
        self._spawn_locks: dict[str, asyncio.Lock] = {}

    def set_runner(
        self,
        runner: "Runner",
        *,
        warm_idle_s: float | None = None,
        heartbeat_timeout_s: float | None = None,
        audit_emit: AuditEmitFn | None = None,
    ) -> None:
        """Wire the real runner after construction.

        Transitioning from the no-runner construction state to a real
        runner requires an `audit_emit`: otherwise `runner_spawned`,
        `runner_warm_idle_evicted`, `runner_heartbeat_lost`, and
        `tool_call.crashed` would silently drop.
        """
        if audit_emit is None and self._audit_emit is None:
            raise ValueError(
                "audit_emit required when a real Runner is wired; "
                "construct without one only for import-clean lifespans. "
                "Pass audit_emit=<AuditRepo.append bridge>."
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
        """Lock-scoped entry removal.

        When `expected_runner_id` is provided, the removal only fires if
        the currently installed entry's handle matches that runner_id.
        Without this guard a stale HeartbeatWatcher belonging to an
        already-evicted runner could race in after a fresh runner had
        been installed for the same session and wipe the new entry — the
        next heartbeat-frame would find an empty slot and the SSE client
        would get an empty 200.
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
            # bounded. Safe under `_lock` because the spawn lock is
            # only acquired by `get_or_spawn`, which would re-create
            # it on the next call.
            self._spawn_locks.pop(session_id, None)
            return entry

    async def _get_entry(self, session_id: str) -> RegistryEntry | None:
        async with self._lock:
            return self._entries.get(session_id)

    async def _get_spawn_lock(self, session_id: str) -> asyncio.Lock:
        """Create-on-miss accessor for the per-session spawn lock.

        `_lock` is held only for the dict mutation; the returned lock is
        acquired by the caller outside `_lock` so the spawn body does not
        widen the registry-map critical section.
        """
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

        # A warm-idle kill task might be mid-flight. Awaiting it while
        # holding `_lock` would deadlock because the kill's `_on_evict`
        # callback re-acquires `_lock`.
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

        # Serialize concurrent spawners per session so N racing callers
        # never produce two containers. Acquired outside `_lock` to keep
        # the kill-task deadlock invariant.
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
            # Stop the heartbeat watcher BEFORE removing the entry. A
            # stale watcher would otherwise keep looping on the
            # terminated handle and ~30s later wipe whichever entry now
            # occupies `session_id` — including a brand-new runner.
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
                # A mid-flight tool call when the runner crashes must
                # surface as `tool_call.crashed`.
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
        """O(1) reverse lookup. The inbound-webhook HMAC-secret lookup
        runs once per event and cannot be O(n) over the registry."""
        async with self._lock:
            sid = self._by_runner_id.get(runner_id)
            if sid is None:
                return None
            return self._entries.get(sid)

    async def inbox_get(
        self, session_id: str, timeout_s: float
    ) -> dict | None:
        """Pull the next inbound frame for `session_id`, or None on
        timeout / no entry.

        Returns None immediately if no entry exists for the session.
        Otherwise waits up to `timeout_s` for the next queued frame.
        Frames are enqueued by `src/api/stream.py:stream_post` when a
        new user message arrives.
        """
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

    # Event hooks invoked by the API layer.

    async def on_heartbeat_frame(self, session_id: str) -> None:
        """Reset the per-session heartbeat-dead timer.

        Method name must end in `_frame` to match the getattr lookup in
        `src/api/internal.py:_dispatch_frame`; without it the getattr
        silently returns None and healthy runners die at heartbeat timeout.
        """
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.heartbeat.beat()

    async def on_run_finished(self, session_id: str) -> None:
        """Start the warm-idle countdown when a `RUN_FINISHED` frame
        arrives."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.warm_idle.on_run_finished()

    async def on_tool_call_started(
        self, session_id: str, tool_call_id: str, tool_name: str
    ) -> None:
        """Record an in-flight tool call on `BeforeToolCall` so
        `_on_crash` can emit `tool_call.crashed` if the runner dies
        before `AfterToolCall`."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.in_flight_tool_calls[tool_call_id] = tool_name

    async def on_tool_call_completed(
        self, session_id: str, tool_call_id: str
    ) -> None:
        """Remove the in-flight record on `AfterToolCall`."""
        entry = await self._get_entry(session_id)
        if entry is None:
            return
        entry.in_flight_tool_calls.pop(tool_call_id, None)
