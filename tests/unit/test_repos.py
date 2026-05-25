"""Repository CRUD unit tests (Phase 1.B14).

These exercise:
- `SessionRepo.create/get/close/touch_updated_at` (AC1, AC3).
- `MessageRepo.append/append_delta/finalize/list_for_session` — including
  the server-side `content = content || $1` UPDATE (AC4c) and per-session
  monotonic `seq` (AC2).
- `AuditRepo.append` runtime-validates the locked 12-name vocabulary.
- `ArtifactRepo.register` ON CONFLICT idempotency (AC23).
- `boot.sweep_orphaned_messages` writes `stream_orphaned` for AC24.

These tests require a real Postgres (the schema relies on
`gen_random_uuid`, JSONB, partial indexes, named ENUM). If `TEST_DATABASE_URL`
isn't set and the default localhost DB isn't reachable, the module skips.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import AUDIT_EVENT_TYPES, AuditLog
from src.repos.artifacts import ArtifactRepo
from src.repos.audit import AuditRepo
from src.repos.boot import sweep_orphaned_messages
from src.repos.messages import MessageRepo
from src.repos.sessions import SessionRepo


pytestmark = pytest.mark.unit


TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent_test",
)


def _has_postgres() -> bool:
    try:
        engine = create_async_engine(TEST_DATABASE_URL)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_probe(engine))
            return True
        finally:
            loop.run_until_complete(engine.dispose())
            loop.close()
    except Exception:
        return False


async def _probe(engine) -> None:
    async with engine.connect() as conn:
        from sqlalchemy import text

        await conn.execute(text("SELECT 1"))


if not _has_postgres():  # pragma: no cover - environment-gated
    pytest.skip(
        f"Test Postgres unreachable at {TEST_DATABASE_URL} — set TEST_DATABASE_URL or run compose",
        allow_module_level=True,
    )


@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(_text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _text(s: str):
    from sqlalchemy import text

    return text(s)


# ---------- Sessions ----------


async def test_session_create_get_close(db_session) -> None:
    repo = SessionRepo(db_session)
    row = await repo.create(analyst_id="ana-1", title="hello")
    await db_session.commit()

    got = await repo.get(row.id)
    assert got is not None
    assert got.analyst_id == "ana-1"
    assert got.title == "hello"
    assert got.closed_at is None

    await repo.close(row.id)
    await db_session.commit()
    db_session.expunge_all()

    got2 = await repo.get(row.id)
    assert got2 is not None
    assert got2.closed_at is not None


# ---------- Messages ----------


async def test_message_append_seq_monotonic(db_session) -> None:
    s_repo = SessionRepo(db_session)
    m_repo = MessageRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    await db_session.commit()

    a = await m_repo.append(session_id=s.id, role="user", content="hi", finalize=True)
    b = await m_repo.append(session_id=s.id, role="assistant", content="")
    c = await m_repo.append(session_id=s.id, role="user", content="hi2", finalize=True)
    await db_session.commit()

    assert (a.seq, b.seq, c.seq) == (1, 2, 3)


async def test_append_delta_concats_server_side(db_session) -> None:
    """AC4c: `content = content || $1` UPDATE."""
    s_repo = SessionRepo(db_session)
    m_repo = MessageRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    msg = await m_repo.append(session_id=s.id, role="assistant", content="")
    await db_session.commit()

    await m_repo.append_delta(msg.id, "Hello, ")
    await m_repo.append_delta(msg.id, "world!")
    await db_session.commit()

    got = await m_repo.get(msg.id)
    assert got is not None
    assert got.content == "Hello, world!"
    assert got.finalized_at is None

    await m_repo.finalize(msg.id, token_count=2)
    await db_session.commit()
    db_session.expunge_all()

    got2 = await m_repo.get(msg.id)
    assert got2 is not None
    assert got2.finalized_at is not None
    assert got2.token_count == 2


async def test_list_for_session_ordered_by_seq(db_session) -> None:
    s_repo = SessionRepo(db_session)
    m_repo = MessageRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    for i in range(5):
        await m_repo.append(
            session_id=s.id, role="user", content=f"m{i}", finalize=True
        )
    await db_session.commit()

    page1 = await m_repo.list_for_session(s.id, limit=3)
    assert [m.content for m in page1] == ["m0", "m1", "m2"]

    page2 = await m_repo.list_for_session(s.id, after_seq=page1[-1].seq, limit=3)
    assert [m.content for m in page2] == ["m3", "m4"]


# ---------- Audit ----------


async def test_audit_append_rejects_unknown_event(db_session) -> None:
    s_repo = SessionRepo(db_session)
    a_repo = AuditRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    await db_session.commit()

    with pytest.raises(ValueError, match="unknown audit event_type"):
        await a_repo.append(
            event_type="totally_made_up", actor="backend", session_id=s.id
        )


async def test_audit_append_all_locked_names_accepted(db_session) -> None:
    s_repo = SessionRepo(db_session)
    a_repo = AuditRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    await db_session.commit()

    for name in AUDIT_EVENT_TYPES:
        await a_repo.append(event_type=name, actor="backend", session_id=s.id)
    await db_session.commit()


# ---------- Artifacts ----------


async def test_artifact_register_idempotent(db_session) -> None:
    s_repo = SessionRepo(db_session)
    art_repo = ArtifactRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    await db_session.commit()

    sha = hashlib.sha256(b"hello").digest()
    first, created1 = await art_repo.register(
        session_id=s.id,
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=42,
        bucket="kloc-agent-artifacts-dev",
        object_key=f"sessions/{s.id}/artifacts/abc/report.pdf",
        sha256=sha,
    )
    await db_session.commit()
    assert created1 is True

    second, created2 = await art_repo.register(
        session_id=s.id,
        filename="report.pdf",
        content_type="application/pdf",
        size_bytes=42,
        bucket="kloc-agent-artifacts-dev",
        object_key=f"sessions/{s.id}/artifacts/abc/report.pdf",
        sha256=sha,
    )
    await db_session.commit()
    assert created2 is False
    assert second.id == first.id


# ---------- Boot recovery ----------


async def test_sweep_orphaned_messages_marks_stream_orphaned(db_session) -> None:
    """AC24: backend boot scans `finalized_at IS NULL` and writes
    `stream_orphaned` audit rows."""
    s_repo = SessionRepo(db_session)
    m_repo = MessageRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    orphan = await m_repo.append(
        session_id=s.id, role="assistant", content="partial"
    )
    finalized = await m_repo.append(
        session_id=s.id, role="user", content="done", finalize=True
    )
    await db_session.commit()
    assert orphan.finalized_at is None
    assert finalized.finalized_at is not None

    # boot.sweep_orphaned_messages runs against a raw Connection, not a Session.
    # engine.begin() wraps the call in a transaction; the sweeper just executes.
    bind = db_session.bind
    async with bind.begin() as conn:
        count = await sweep_orphaned_messages(conn)

    assert count == 1
    db_session.expunge_all()
    refreshed = await m_repo.get(orphan.id)
    assert refreshed is not None
    assert refreshed.finalized_at is not None

    # Audit row written.
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "stream_orphaned")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].message_id == orphan.id
