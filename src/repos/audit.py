"""Audit log repository.

Canonical event-type vocabulary lives in `src.db.models.AuditEventType`.
`append` takes a string but runtime-validates it against
`AUDIT_EVENT_TYPES` so a typo surfaces in tests rather than at the next
audit query.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AUDIT_EVENT_TYPES, AuditLog


class AuditRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        event_type: str,
        actor: str,
        session_id: uuid.UUID | None = None,
        message_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AuditLog:
        if event_type not in AUDIT_EVENT_TYPES:
            raise ValueError(
                f"unknown audit event_type {event_type!r}; allowed: "
                f"{sorted(AUDIT_EVENT_TYPES)}"
            )
        row = AuditLog(
            session_id=session_id,
            message_id=message_id,
            event_type=event_type,
            actor=actor,
            payload=payload or {},
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def last_state_snapshot(
        self, session_id: uuid.UUID
    ) -> AuditLog | None:
        """Return the most recent state snapshot row for a session.

        Snapshots are stored with payload `{"kind": "state_snapshot", ...}`
        on an audit row; the runner consumes the latest one on rehydrate.
        """
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.session_id == session_id,
                AuditLog.payload.op("@>")({"kind": "state_snapshot"}),
            )
            .order_by(desc(AuditLog.created_at))
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_session(
        self,
        session_id: uuid.UUID,
        after_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[AuditLog]:
        stmt = select(AuditLog).where(AuditLog.session_id == session_id)
        if after_id is not None:
            stmt = stmt.where(AuditLog.id > after_id)
        stmt = stmt.order_by(AuditLog.created_at.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars())
