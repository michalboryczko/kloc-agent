"""Boot-time recovery helpers (Phase 1.B13 / AC24).

On backend startup, any `messages.finalized_at IS NULL` is an orphaned
stream from a prior process. Write a `stream_orphaned` audit row per
orphan and mark the message finalized so subsequent reads aren't
indefinitely stuck in 'streaming' state.
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from src.db.models import AuditLog, Message


async def sweep_orphaned_messages(conn: AsyncConnection) -> int:
    """Returns number of orphaned messages swept."""
    result = await conn.execute(
        select(Message.id, Message.session_id).where(
            Message.finalized_at.is_(None)
        )
    )
    orphans = list(result.all())
    if not orphans:
        return 0

    for msg_id, session_id in orphans:
        await conn.execute(
            AuditLog.__table__.insert().values(
                session_id=session_id,
                message_id=msg_id,
                event_type="stream_orphaned",
                actor="backend",
                payload={"reason": "boot_recovery"},
            )
        )

    orphan_ids = [m[0] for m in orphans]
    await conn.execute(
        update(Message)
        .where(Message.id.in_(orphan_ids))
        .values(finalized_at=Message.created_at)
    )
    return len(orphan_ids)
