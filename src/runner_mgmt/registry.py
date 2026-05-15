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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .heartbeat import HeartbeatWatcher
from .warm_idle import WarmIdleManager

if TYPE_CHECKING:
    from .protocol import Runner, RunnerHandle

log = logging.getLogger(__name__)


@dataclass
class RegistryEntry:
    handle: "RunnerHandle"
    warm_idle: WarmIdleManager
    heartbeat: HeartbeatWatcher
    inbox: asyncio.Queue
    audit_emit: Callable[[str, dict], Awaitable[None]] | None
    # In-flight tool calls: tool_call_id -> tool_name. Populated on
    # BeforeToolCall webhook receipt (dev-1's src/api/webhooks.py via
    # registry.on_tool_call_started); cleared on AfterToolCall via
    # on_tool_call_completed. Used by `_on_crash` to emit
    # `tool_call.crashed` per plan §575 / AC20.
    in_flight_tool_calls: dict[str, str] = field(default_factory=dict)


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
        self._lock = asyncio.Lock()

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

    async def _remove_entry(self, session_id: str) -> RegistryEntry | None:
        """Lock-scoped helper. Used by `_on_evict` / `_on_crash` and by
        get_or_spawn's "container died" branch."""
        async with self._lock:
            return self._entries.pop(session_id, None)

    async def _get_entry(self, session_id: str) -> RegistryEntry | None:
        async with self._lock:
            return self._entries.get(session_id)

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
            if entry is not None and await self._runner.is_alive(entry.handle):
                return entry
            # Either entry was evicted by the kill task, or the container
            # is dead despite the entry still existing (rare; e.g. crash
            # without heartbeat-loss yet). Drop and respawn.
            if entry is not None:
                await self._remove_entry(session_id)

        # No live container — spawn fresh.
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
            await self._remove_entry(session_id)
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
            await self._remove_entry(session_id)
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
        return entry

    async def get(self, session_id: str) -> RegistryEntry | None:
        return await self._get_entry(session_id)

    async def get_by_runner_id(self, runner_id: str) -> RegistryEntry | None:
        """Used by `src/api/internal.py` to authenticate inbound JSONL
        events: the runner identifies itself in the URL, the backend
        looks up the session it belongs to."""
        async with self._lock:
            for entry in self._entries.values():
                if getattr(entry.handle, "runner_id", None) == runner_id:
                    return entry
            return None

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
