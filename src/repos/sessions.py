"""Session repository."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Session


class SessionRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        analyst_id: str,
        title: str = "Untitled session",
        metadata: dict | None = None,
    ) -> Session:
        row = Session(
            analyst_id=analyst_id,
            title=title,
            metadata_=metadata or {},
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, session_id: uuid.UUID) -> Session | None:
        return await self._session.get(Session, session_id)

    async def touch_updated_at(self, session_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(updated_at=datetime.now(timezone.utc))
        )

    async def close(self, session_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id, Session.closed_at.is_(None))
            .values(closed_at=datetime.now(timezone.utc))
        )

    async def set_runner_state(self, session_id: uuid.UUID, state: str) -> None:
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(runner_state=state)
        )

    async def list_for_analyst(
        self, analyst_id: str, include_closed: bool = False
    ) -> list[Session]:
        stmt = select(Session).where(Session.analyst_id == analyst_id)
        if not include_closed:
            stmt = stmt.where(Session.closed_at.is_(None))
        stmt = stmt.order_by(Session.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars())
