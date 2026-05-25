"""Tool-call repository.

`upsert_started` is the only path that allocates a row id (and a `seq`
inside the parent message). Re-emission of `TOOL_CALL_START` (e.g.
across an SSE drop + resume) is absorbed by the
`(session_id, tool_call_id)` UNIQUE — `ON CONFLICT DO UPDATE` returns
the original row unchanged.

`append_args_delta` is a server-side `args = args || $delta` UPDATE
keyed by `(session_id, tool_call_id)`; matches the text-content
debouncer's hot path on `messages.content`.

`mark_done` and `mark_denied` are state-aware: `mark_done` skips rows
already in `denied` so a late-arriving END cannot overwrite a denial.
`mark_denied` is idempotent — same row stays denied.

`set_result` truncates per `KLOC_TOOL_LIMITS.result_max_bytes` with a
`…[truncated]` marker mirroring `runner/offload.py`. Truncation is a
storage choice; live SSE delivery does not pay this cost.

`list_for_messages` is the API read path's batched query — one shot
keyed by `parent_message_id IN (...)`, grouped in Python.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ToolCall
from src.settings import get_settings


log = logging.getLogger(__name__)


_TRUNCATION_MARKER = "…[truncated]"


def _truncate_result(value: str, cap: int) -> str:
    if cap <= 0 or len(value) <= cap:
        return value
    marker = _TRUNCATION_MARKER
    head = max(0, cap - len(marker))
    return value[:head] + marker


class ToolCallRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_started(
        self,
        session_id: uuid.UUID,
        parent_message_id: uuid.UUID,
        tool_call_id: str,
        tool_name: str,
    ) -> uuid.UUID:
        # Per T06 Notes: race-on-seq is acceptable; the UI ties duplicate
        # seq values within one parent on created_at.
        next_seq_stmt = select(
            func.coalesce(func.max(ToolCall.seq), 0) + 1
        ).where(ToolCall.parent_message_id == parent_message_id)
        next_seq = int((await self._session.execute(next_seq_stmt)).scalar_one())

        stmt = (
            pg_insert(ToolCall)
            .values(
                session_id=session_id,
                parent_message_id=parent_message_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                state="running",
                seq=next_seq,
            )
            .on_conflict_do_update(
                constraint="uq_tool_calls_session_tool_id",
                set_={"tool_name": tool_name},
            )
            .returning(ToolCall.id)
        )
        row_id = (await self._session.execute(stmt)).scalar_one()
        await self._session.flush()
        return row_id

    async def append_args_delta(
        self,
        session_id: uuid.UUID,
        tool_call_id: str,
        delta: str,
    ) -> None:
        result = await self._session.execute(
            update(ToolCall)
            .where(
                ToolCall.session_id == session_id,
                ToolCall.tool_call_id == tool_call_id,
            )
            .values(args=ToolCall.args + delta)
        )
        if result.rowcount == 0:
            # START was never persisted (dropped frame, sequence skew).
            # Logging keeps the symptom visible without breaking the run.
            log.warning(
                "tool_call_repo.append_args_delta.no_row "
                "session=%s tool_call_id=%s",
                session_id,
                tool_call_id,
            )
        await self._session.flush()

    async def mark_done(
        self,
        session_id: uuid.UUID,
        tool_call_id: str,
    ) -> None:
        # `state = 'running'` guard: a late END after a deny must not
        # un-deny the row.
        await self._session.execute(
            update(ToolCall)
            .where(
                ToolCall.session_id == session_id,
                ToolCall.tool_call_id == tool_call_id,
                ToolCall.state == "running",
            )
            .values(
                state="done",
                finalized_at=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()

    async def set_result(
        self,
        session_id: uuid.UUID,
        tool_call_id: str,
        result: str,
    ) -> None:
        cap = get_settings().tool_limits.result_max_bytes
        truncated = _truncate_result(result, cap)
        await self._session.execute(
            update(ToolCall)
            .where(
                ToolCall.session_id == session_id,
                ToolCall.tool_call_id == tool_call_id,
            )
            .values(result=truncated)
        )
        await self._session.flush()

    async def mark_denied(
        self,
        session_id: uuid.UUID,
        tool_call_id: str,
        reason: str | None,
        hint: str | None,
    ) -> None:
        await self._session.execute(
            update(ToolCall)
            .where(
                ToolCall.session_id == session_id,
                ToolCall.tool_call_id == tool_call_id,
            )
            .values(
                state="denied",
                denied_reason=reason,
                denied_hint=hint,
                finalized_at=datetime.now(timezone.utc),
            )
        )
        await self._session.flush()

    async def list_for_messages(
        self,
        session_id: uuid.UUID,
        message_ids: list[uuid.UUID],
    ) -> list[ToolCall]:
        if not message_ids:
            return []
        stmt = (
            select(ToolCall)
            .where(
                ToolCall.session_id == session_id,
                ToolCall.parent_message_id.in_(message_ids),
            )
            .order_by(ToolCall.parent_message_id, ToolCall.seq, ToolCall.created_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())
