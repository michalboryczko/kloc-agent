"""Integration tests for the session REST surface (Track C, C1-1).

Real Postgres, in-process FastAPI backend (no runner needed for these).
Covers AC1, AC2, AC3, AC24-adjacent.
"""
from __future__ import annotations

import uuid

import pytest

from tests.fixtures.audit_events import (
    SESSION_CLOSED,
    SESSION_OPENED,
)


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_create_session_returns_201_with_uuid(
    asgi_client, db_session, truncate_all_tables
):
    """POST /v1/sessions persists a row and returns the session_id (AC1)."""
    resp = await asgi_client.post("/v1/sessions", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "session_id" in body
    assert "created_at" in body
    session_id = uuid.UUID(body["session_id"])

    # Audit row session_opened persisted in same tx as session row.
    from sqlalchemy import select

    from src.db.models import AuditLog, Session

    s = await db_session.execute(
        select(Session).where(Session.id == session_id)
    )
    assert s.scalar_one().analyst_id == "analyst-poc"

    a = await db_session.execute(
        select(AuditLog).where(
            AuditLog.session_id == session_id,
            AuditLog.event_type == SESSION_OPENED,
        )
    )
    audit_rows = list(a.scalars())
    assert len(audit_rows) == 1
    assert audit_rows[0].actor == "backend"


@pytest.mark.asyncio
async def test_get_session_returns_state_metadata(
    asgi_client, truncate_all_tables
):
    """GET /v1/sessions/{id} returns status, runner_state, metadata (AC2)."""
    create = await asgi_client.post(
        "/v1/sessions",
        json={"title": "Investigation 42", "metadata": {"k": "v"}},
    )
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    resp = await asgi_client.get(f"/v1/sessions/{session_id}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == session_id
    assert detail["title"] == "Investigation 42"
    assert detail["analyst_id"] == "analyst-poc"
    assert detail["runner_state"] == "idle"
    assert detail["closed_at"] is None
    assert detail["metadata"] == {"k": "v"}


@pytest.mark.asyncio
async def test_get_session_404_for_unknown(asgi_client, truncate_all_tables):
    """GET /v1/sessions/{id} returns 404 for nonexistent."""
    resp = await asgi_client.get(f"/v1/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_messages_paginates_by_seq_cursor(
    asgi_client, truncate_all_tables
):
    """GET /v1/sessions/{id}/messages?after=<seq>&limit=N orders by seq (AC2)."""
    create = await asgi_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    # Post 3 user messages.
    for i in range(3):
        post = await asgi_client.post(
            f"/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": f"msg{i}"},
        )
        assert post.status_code == 202, post.text

    # Page 1: first 2 messages.
    page1 = await asgi_client.get(
        f"/v1/sessions/{session_id}/messages", params={"limit": 2}
    )
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["messages"]) == 2
    assert body1["has_more"] is True
    assert body1["next_cursor"] == body1["messages"][-1]["seq"]
    assert [m["content"] for m in body1["messages"]] == ["msg0", "msg1"]

    # Page 2: remaining 1 message, cursor-driven.
    page2 = await asgi_client.get(
        f"/v1/sessions/{session_id}/messages",
        params={"after": body1["next_cursor"], "limit": 2},
    )
    body2 = page2.json()
    assert len(body2["messages"]) == 1
    assert body2["has_more"] is False
    assert body2["messages"][0]["content"] == "msg2"


@pytest.mark.asyncio
async def test_post_message_persists_before_returning(
    asgi_client, db_session, truncate_all_tables
):
    """POST /v1/sessions/{id}/messages commits user message BEFORE 202 (AC4a)."""
    create = await asgi_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    resp = await asgi_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "find handlers"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "run_id" in body and "message_id" in body
    assert body["stream_url"].startswith(f"/v1/sessions/{session_id}/stream")

    # Row must be visible via a fresh query (proves commit, not just flush).
    from sqlalchemy import select

    from src.db.models import Message

    db_session.expire_all()
    r = await db_session.execute(
        select(Message).where(
            Message.session_id == uuid.UUID(session_id),
            Message.role == "user",
        )
    )
    row = r.scalar_one()
    assert row.content == "find handlers"
    assert row.finalized_at is not None, "user message should be finalized on persist"
    assert row.seq == 1


@pytest.mark.asyncio
async def test_post_message_rejects_non_user_role(
    asgi_client, truncate_all_tables
):
    create = await asgi_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    resp = await asgi_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "assistant", "content": "x"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_close_session_is_idempotent(
    asgi_client, db_session, truncate_all_tables
):
    """POST /v1/sessions/{id}/close twice returns 204 both times + 1 audit row (AC3)."""
    create = await asgi_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    a = await asgi_client.post(f"/v1/sessions/{session_id}/close")
    b = await asgi_client.post(f"/v1/sessions/{session_id}/close")
    assert a.status_code == 204
    assert b.status_code == 204

    from sqlalchemy import select

    from src.db.models import AuditLog

    db_session.expire_all()
    r = await db_session.execute(
        select(AuditLog).where(
            AuditLog.session_id == uuid.UUID(session_id),
            AuditLog.event_type == SESSION_CLOSED,
        )
    )
    rows = list(r.scalars())
    assert len(rows) == 1, "idempotent close must not emit duplicate audit rows"


@pytest.mark.asyncio
async def test_post_message_to_closed_session_rejected(
    asgi_client, truncate_all_tables
):
    create = await asgi_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]
    await asgi_client.post(f"/v1/sessions/{session_id}/close")

    resp = await asgi_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "x"},
    )
    assert resp.status_code == 409
