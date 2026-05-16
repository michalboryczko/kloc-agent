"""`audit_emit` closure factory.

`RunnerRegistry`, `WarmIdleManager`, and `HeartbeatWatcher` emit audit
events (`runner_spawned`, `runner_warm_idle_evicted`,
`runner_heartbeat_lost`, `tool_call.crashed`) via a callback so they
stay free of FastAPI / SQLAlchemy imports. Lifespan injects a closure
that opens a fresh `AsyncSession` per emit and commits inline — one DB
tx per audit row, no cross-task session sharing.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from src.repos.audit import AuditRepo


log = logging.getLogger(__name__)


AuditEmitFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def make_audit_emit_closure(
    sessionmaker: async_sessionmaker,
    actor: str = "backend",
) -> AuditEmitFn:
    """Return an `(event_type, payload) -> None` async callback.

    Signature matches `RunnerRegistry`'s `AuditEmitFn` Protocol:
    `(event_type: str, payload: dict) -> Awaitable[None]`. `session_id`
    is extracted from `payload` so runner-management code does not have
    to thread the UUID into every call site.

    Each emit opens its own session, transaction, and commit. Failures
    are logged and swallowed so a failed audit write never crashes
    runner management.
    """

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        # The runner-management layer puts session_id inside `payload`
        # (per its existing call sites). We extract + normalize.
        sid_raw = payload.get("session_id")
        session_id: uuid.UUID | None = None
        if sid_raw:
            try:
                session_id = uuid.UUID(str(sid_raw))
            except (ValueError, TypeError):
                session_id = None

        try:
            async with sessionmaker() as db:
                await AuditRepo(db).append(
                    event_type=event_type,
                    actor=actor,
                    session_id=session_id,
                    payload=payload,
                )
                await db.commit()
        except Exception:
            log.exception(
                "audit_emit_failed",
                extra={"event_type": event_type, "session_id": str(sid_raw)},
            )

    return emit
