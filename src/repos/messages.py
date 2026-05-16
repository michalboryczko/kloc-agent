"""Message repository.

The hot path is `append_delta`: a server-side concat UPDATE avoids
re-sending the full body on every batched flush.

`append` is the user/assistant/tool insert path. `seq` is calculated
inside the same transaction as the INSERT via `SELECT max(seq) + 1` so
wall-clock skew between runner and backend cannot break ordering.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog, Message, MessageRole


_MAX_SEQ_RETRIES = 5


class MessageRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _next_seq(self, session_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(Message.seq), 0)).where(
                Message.session_id == session_id
            )
        )
        return int(result.scalar_one()) + 1

    async def append(
        self,
        session_id: uuid.UUID,
        role: MessageRole,
        content: str = "",
        content_parts: dict | None = None,
        model: str | None = None,
        parent_message_id: uuid.UUID | None = None,
        finalize: bool = False,
    ) -> Message:
        """Insert a Message. For streaming assistant messages, leave
        `finalize=False`; the streaming layer later calls `finalize()` and
        meanwhile appends content via `append_delta`.

        `_next_seq` + INSERT is not atomic at the SQL level (two
        SELECT max(seq)+1 reads can race); the per-session UNIQUE
        constraint `uq_messages_session_seq` is the actual atomic
        guarantee. On UNIQUE violation (concurrent insert won the race)
        we rollback the savepoint and retry up to `_MAX_SEQ_RETRIES`
        times with a fresh seq read.
        """
        last_err: IntegrityError | None = None
        for _ in range(_MAX_SEQ_RETRIES):
            sp = await self._session.begin_nested()
            row = Message(
                session_id=session_id,
                role=role,
                content=content,
                content_parts=content_parts,
                model=model,
                parent_message_id=parent_message_id,
                seq=await self._next_seq(session_id),
                finalized_at=datetime.now(timezone.utc) if finalize else None,
            )
            self._session.add(row)
            try:
                await self._session.flush()
            except IntegrityError as e:
                last_err = e
                await sp.rollback()
                continue
            await sp.commit()
            return row
        # Exhausted retries — re-raise the last IntegrityError so the
        # caller sees the real DB error rather than a silent failure.
        assert last_err is not None
        raise last_err

    async def append_delta(
        self,
        message_id: uuid.UUID,
        delta: str,
    ) -> None:
        """Server-side `content = content || $1` UPDATE.

        Called by the 256-char / 250-ms debouncer in
        `src/streaming/debounce.py`; the streaming layer batches many
        token deltas into one buffered UPDATE.
        """
        await self._session.execute(
            update(Message)
            .where(Message.id == message_id)
            .values(content=Message.content + delta)
        )

    async def finalize(
        self,
        message_id: uuid.UUID,
        token_count: int | None = None,
    ) -> None:
        """Mark a message finalized and emit a `message_persisted` audit
        row in the SAME transaction.

        Idempotent: if the message is already finalized, no audit row is
        written (avoids duplicate rows on debouncer flush + explicit
        finalize).
        """
        values: dict = {"finalized_at": datetime.now(timezone.utc)}
        if token_count is not None:
            values["token_count"] = token_count

        # Capture session_id + prior finalized_at in one query so we can
        # both decide whether to emit and pin the audit row's session_id.
        existing = (
            await self._session.execute(
                select(Message.session_id, Message.finalized_at).where(
                    Message.id == message_id
                )
            )
        ).first()
        if existing is None:
            # Nothing to finalize — degrade silently rather than raise.
            return
        session_id, prior_finalized_at = existing

        await self._session.execute(
            update(Message).where(Message.id == message_id).values(**values)
        )

        if prior_finalized_at is None:
            self._session.add(
                AuditLog(
                    session_id=session_id,
                    message_id=message_id,
                    event_type="message_persisted",
                    actor="backend",
                    payload={"token_count": token_count} if token_count else {},
                )
            )
            await self._session.flush()

    async def get(self, message_id: uuid.UUID) -> Message | None:
        return await self._session.get(Message, message_id)

    async def list_for_session(
        self,
        session_id: uuid.UUID,
        after_seq: int | None = None,
        limit: int = 100,
    ) -> list[Message]:
        """Ordered by per-session monotonic `seq`."""
        stmt = select(Message).where(Message.session_id == session_id)
        if after_seq is not None:
            stmt = stmt.where(Message.seq > after_seq)
        stmt = stmt.order_by(Message.seq.asc()).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars())
