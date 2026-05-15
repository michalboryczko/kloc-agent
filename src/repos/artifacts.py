"""Artifact metadata repository (Phase 1.B11).

`register` is idempotent via `UNIQUE (session_id, object_key)` — retries
of the runner's upload webhook collapse to no-op (AC23).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ArtifactMetadata


class ArtifactRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        session_id: uuid.UUID,
        filename: str,
        content_type: str,
        size_bytes: int,
        bucket: str,
        object_key: str,
        sha256: bytes,
        message_id: uuid.UUID | None = None,
    ) -> tuple[ArtifactMetadata, bool]:
        """Idempotent insert; returns (row, created).

        Uses ON CONFLICT (session_id, object_key) DO NOTHING so duplicate
        webhooks from the runner collapse cleanly (AC23 idempotency).
        """
        stmt = (
            pg_insert(ArtifactMetadata)
            .values(
                session_id=session_id,
                message_id=message_id,
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                bucket=bucket,
                object_key=object_key,
                sha256=sha256,
            )
            .on_conflict_do_nothing(
                index_elements=["session_id", "object_key"],
            )
            .returning(ArtifactMetadata)
        )
        result = await self._session.execute(stmt)
        inserted = result.scalar_one_or_none()
        if inserted is not None:
            return inserted, True

        # Conflict — return the existing row.
        existing = await self._session.execute(
            select(ArtifactMetadata).where(
                ArtifactMetadata.session_id == session_id,
                ArtifactMetadata.object_key == object_key,
            )
        )
        return existing.scalar_one(), False

    async def get(self, artifact_id: uuid.UUID) -> ArtifactMetadata | None:
        return await self._session.get(ArtifactMetadata, artifact_id)

    async def list_for_session(
        self, session_id: uuid.UUID
    ) -> list[ArtifactMetadata]:
        stmt = (
            select(ArtifactMetadata)
            .where(ArtifactMetadata.session_id == session_id)
            .order_by(ArtifactMetadata.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())
