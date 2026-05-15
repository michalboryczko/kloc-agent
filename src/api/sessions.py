"""Session lifecycle REST endpoints (Phase 1.C-1.1).

POST /v1/sessions               -> 201 {session_id, created_at}      (AC1)
GET  /v1/sessions/{id}          -> 200 {id, status, ...}              (AC2)
GET  /v1/sessions/{id}/messages -> 200 {messages, next_cursor, ...}   (AC2)
POST /v1/sessions/{id}/messages -> 202 {run_id, stream_url}           (AC4a — persist before forward)
POST /v1/sessions/{id}/close    -> 204                                (AC3)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.deps import get_session
from src.repos.audit import AuditRepo
from src.repos.messages import MessageRepo
from src.repos.sessions import SessionRepo


router = APIRouter(tags=["sessions"])


# PoC: single hardcoded analyst (spec § Out of scope: multi-tenant auth).
HARDCODED_ANALYST_ID = "analyst-poc"


class CreateSessionBody(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateSessionResponse(BaseModel):
    session_id: uuid.UUID
    created_at: datetime


class SessionDetail(BaseModel):
    id: uuid.UUID
    analyst_id: str
    title: str
    runner_state: str
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


class MessageOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    content_parts: dict[str, Any] | None
    model: str | None
    seq: int
    created_at: datetime
    finalized_at: datetime | None


class MessagesPage(BaseModel):
    messages: list[MessageOut]
    next_cursor: int | None
    has_more: bool


class PostMessageBody(BaseModel):
    role: str = "user"
    content: str
    content_parts: dict[str, Any] | None = None


class PostMessageResponse(BaseModel):
    run_id: uuid.UUID
    message_id: uuid.UUID
    stream_url: str


@router.post(
    "/sessions",
    response_model=CreateSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    body: CreateSessionBody | None = None,
    db: AsyncSession = Depends(get_session),
) -> CreateSessionResponse:
    body = body or CreateSessionBody()
    sessions = SessionRepo(db)
    audit = AuditRepo(db)
    row = await sessions.create(
        analyst_id=HARDCODED_ANALYST_ID,
        title=body.title or "Untitled session",
        metadata=body.metadata,
    )
    await audit.append(
        event_type="session_opened",
        actor="backend",
        session_id=row.id,
        payload={"analyst_id": HARDCODED_ANALYST_ID},
    )
    await db.commit()
    return CreateSessionResponse(session_id=row.id, created_at=row.created_at)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session_detail(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> SessionDetail:
    sessions = SessionRepo(db)
    row = await sessions.get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    return SessionDetail(
        id=row.id,
        analyst_id=row.analyst_id,
        title=row.title,
        runner_state=row.runner_state,
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=row.metadata_,
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=MessagesPage,
)
async def list_messages(
    session_id: uuid.UUID,
    after: int | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_session),
) -> MessagesPage:
    sessions = SessionRepo(db)
    if (await sessions.get(session_id)) is None:
        raise HTTPException(status_code=404, detail="session not found")

    limit = max(1, min(limit, 500))
    messages_repo = MessageRepo(db)
    # Fetch limit+1 to know whether there's another page.
    rows = await messages_repo.list_for_session(
        session_id=session_id, after_seq=after, limit=limit + 1
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = rows[-1].seq if (has_more and rows) else None
    return MessagesPage(
        messages=[
            MessageOut(
                id=m.id,
                session_id=m.session_id,
                role=m.role,
                content=m.content,
                content_parts=m.content_parts,
                model=m.model,
                seq=m.seq,
                created_at=m.created_at,
                finalized_at=m.finalized_at,
            )
            for m in rows
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.post(
    "/sessions/{session_id}/messages",
    response_model=PostMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_message(
    session_id: uuid.UUID,
    body: PostMessageBody,
    db: AsyncSession = Depends(get_session),
) -> PostMessageResponse:
    """Persist user message BEFORE forwarding to runner (AC4a).

    Returns `{run_id, stream_url}` so the frontend can open the SSE GET
    or POST RunAgentInput envelope. dev-2 owns `/v1/sessions/{id}/stream`
    which actually drives the runner.
    """
    sessions = SessionRepo(db)
    session_row = await sessions.get(session_id)
    if session_row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session_row.closed_at is not None:
        raise HTTPException(status_code=409, detail="session is closed")

    if body.role != "user":
        raise HTTPException(
            status_code=400, detail="only role='user' accepted on /messages"
        )

    messages_repo = MessageRepo(db)
    msg = await messages_repo.append(
        session_id=session_id,
        role="user",
        content=body.content,
        content_parts=body.content_parts,
        finalize=True,
    )
    await sessions.touch_updated_at(session_id)
    await db.commit()

    run_id = uuid.uuid4()
    stream_url = f"/v1/sessions/{session_id}/stream?run_id={run_id}"
    return PostMessageResponse(
        run_id=run_id,
        message_id=msg.id,
        stream_url=stream_url,
    )


@router.post(
    "/sessions/{session_id}/close",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def close_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_session),
) -> None:
    sessions = SessionRepo(db)
    audit = AuditRepo(db)
    row = await sessions.get(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="session not found")
    if row.closed_at is not None:
        return None
    await sessions.close(session_id)
    await audit.append(
        event_type="session_closed",
        actor="backend",
        session_id=session_id,
    )
    await db.commit()
    return None
