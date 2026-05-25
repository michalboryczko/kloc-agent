"""Tests for `src/repos/tool_calls.py:ToolCallRepo` against real Postgres.

Same pattern as `tests/unit/test_repos.py`: drops + recreates the schema
on every fixture so each test sees a clean ring of tables. Skips when
`TEST_DATABASE_URL` is unreachable so `pytest tests/ -q` stays green
without compose.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.models import ToolCall
from src.repos.messages import MessageRepo
from src.repos.sessions import SessionRepo
from src.repos.tool_calls import ToolCallRepo


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
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


if not _has_postgres():  # pragma: no cover - environment-gated
    pytest.skip(
        f"Test Postgres unreachable at {TEST_DATABASE_URL}",
        allow_module_level=True,
    )


@pytest.fixture
async def db_session():
    from sqlalchemy import text

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


async def _seed(db_session) -> tuple[uuid.UUID, uuid.UUID]:
    """Create one Session + one assistant Message; return their ids."""
    s_repo = SessionRepo(db_session)
    m_repo = MessageRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    msg = await m_repo.append(
        session_id=s.id, role="assistant", content="", finalize=False
    )
    await db_session.commit()
    return s.id, msg.id


# ---------------------------------------------------------------------------


async def test_upsert_started_inserts_new_row_with_seq_1(db_session) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    row_id = await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="kloc_search_code",
    )
    await db_session.commit()
    assert row_id is not None

    row = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.id == row_id)
        )
    ).scalar_one()
    assert row.tool_call_id == "tc-1"
    assert row.tool_name == "kloc_search_code"
    assert row.state == "running"
    assert row.args == ""
    assert row.seq == 1
    assert row.parent_message_id == parent_id


async def test_upsert_started_is_idempotent_on_conflict(db_session) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    first = await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="kloc_search_code",
    )
    await db_session.commit()
    second = await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="kloc_search_code_v2",
    )
    await db_session.commit()
    assert first == second, "ON CONFLICT must return the original row id"

    rows = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.session_id == session_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    # The conflict path updates tool_name (used to refresh a renamed tool).
    assert rows[0].tool_name == "kloc_search_code_v2"


async def test_append_args_delta_concatenates(db_session) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="kloc_search_code",
    )
    await db_session.commit()

    await repo.append_args_delta(session_id, "tc-1", '{"query":"')
    await repo.append_args_delta(session_id, "tc-1", 'JWT_SECRET"}')
    await db_session.commit()

    row = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.tool_call_id == "tc-1")
        )
    ).scalar_one()
    assert row.args == '{"query":"JWT_SECRET"}'


async def test_mark_done_after_running_sets_finalized_at(db_session) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="kloc_search_code",
    )
    await db_session.commit()

    await repo.mark_done(session_id, "tc-1")
    await db_session.commit()

    row = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.tool_call_id == "tc-1")
        )
    ).scalar_one()
    assert row.state == "done"
    assert row.finalized_at is not None


async def test_mark_done_skips_denied_rows(db_session) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="read_file",
    )
    await db_session.commit()

    await repo.mark_denied(
        session_id, "tc-1", reason="policy_denied", hint="use byte-range"
    )
    await db_session.commit()

    await repo.mark_done(session_id, "tc-1")
    await db_session.commit()

    row = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.tool_call_id == "tc-1")
        )
    ).scalar_one()
    assert row.state == "denied", "mark_done must not un-deny a denied row"
    assert row.denied_reason == "policy_denied"


async def test_set_result_truncates_above_cap(db_session, monkeypatch) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="read_file",
    )
    await db_session.commit()

    # Drop the cap deep below the payload so truncation is observable
    # without writing a 64 KiB blob.
    from src.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    settings.tool_limits.result_max_bytes = 32  # type: ignore[misc]
    huge = "A" * 1024
    try:
        await repo.set_result(session_id, "tc-1", huge)
        await db_session.commit()
    finally:
        get_settings.cache_clear()

    row = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.tool_call_id == "tc-1")
        )
    ).scalar_one()
    assert row.result is not None
    assert len(row.result) <= 32
    assert row.result.endswith("…[truncated]")


async def test_mark_denied_sets_state_and_reason_and_hint(db_session) -> None:
    session_id, parent_id = await _seed(db_session)
    repo = ToolCallRepo(db_session)
    await repo.upsert_started(
        session_id=session_id,
        parent_message_id=parent_id,
        tool_call_id="tc-1",
        tool_name="read_file",
    )
    await db_session.commit()

    await repo.mark_denied(
        session_id,
        "tc-1",
        reason="tool_limit:file_read",
        hint="use start_line/end_line",
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.tool_call_id == "tc-1")
        )
    ).scalar_one()
    assert row.state == "denied"
    assert row.denied_reason == "tool_limit:file_read"
    assert row.denied_hint == "use start_line/end_line"
    assert row.finalized_at is not None

    # Idempotency: a second mark_denied with the same fields is a no-op
    # state-wise.
    await repo.mark_denied(
        session_id,
        "tc-1",
        reason="tool_limit:file_read",
        hint="use start_line/end_line",
    )
    await db_session.commit()
    row2 = (
        await db_session.execute(
            select(ToolCall).where(ToolCall.tool_call_id == "tc-1")
        )
    ).scalar_one()
    assert row2.state == "denied"


async def test_list_for_messages_returns_grouped_by_parent(db_session) -> None:
    s_repo = SessionRepo(db_session)
    m_repo = MessageRepo(db_session)
    s = await s_repo.create(analyst_id="ana-1")
    msg_a = await m_repo.append(
        session_id=s.id, role="assistant", content=""
    )
    msg_b = await m_repo.append(
        session_id=s.id, role="assistant", content=""
    )
    await db_session.commit()

    repo = ToolCallRepo(db_session)
    await repo.upsert_started(s.id, msg_a.id, "tc-a1", "kloc_search_code")
    await repo.upsert_started(s.id, msg_a.id, "tc-a2", "read_file")
    await repo.upsert_started(s.id, msg_b.id, "tc-b1", "kloc_flows")
    await db_session.commit()

    rows = await repo.list_for_messages(s.id, [msg_a.id, msg_b.id])
    assert len(rows) == 3

    by_parent: dict[uuid.UUID, list[str]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_message_id, []).append(r.tool_call_id)

    assert sorted(by_parent[msg_a.id]) == ["tc-a1", "tc-a2"]
    assert by_parent[msg_b.id] == ["tc-b1"]

    # Empty input is safe.
    assert await repo.list_for_messages(s.id, []) == []
