"""Integration-style test for `_persist_events`'s tool-call branches.

Publishes a wire sequence through the in-process event bus and asserts
that one `tool_calls` row is committed with `state='done'`, the right
`args`, the right `result`, and a `parent_message_id` FK pointing at
the assistant Message the persister lazily inserted.

This exercises the same code path the SSE pipeline uses; only the
runner is mocked out (we publish wire events directly).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import Base
from src.db.engine import get_engine, get_sessionmaker
from src.db.models import Message, Session, ToolCall


pytestmark = pytest.mark.integration


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
async def fresh_engine(monkeypatch):
    """Reset the kloc_agent_test schema and rewire `get_engine` /
    `get_sessionmaker` to point at it so `_persist_events` opens its
    own session against the test DB."""
    from sqlalchemy import text

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Override the module-level engine accessors so the persister picks
    # up the test factory.
    monkeypatch.setattr("src.db.engine._engine", engine, raising=False)
    monkeypatch.setattr("src.db.engine._sessionmaker", factory, raising=False)
    monkeypatch.setattr(
        "src.db.engine.get_engine", lambda: engine, raising=False
    )
    monkeypatch.setattr(
        "src.db.engine.get_sessionmaker", lambda: factory, raising=False
    )
    # `src.api.stream` imported the symbols at module load; rebind them.
    monkeypatch.setattr(
        "src.api.stream.get_sessionmaker", lambda: factory, raising=False
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _seed_session(factory) -> uuid.UUID:
    async with factory() as session:
        row = Session(analyst_id="ana-tool-calls")
        session.add(row)
        await session.flush()
        sid = row.id
        await session.commit()
    return sid


async def test_persist_events_writes_tool_call_row_with_parent_fk(
    fresh_engine, monkeypatch
) -> None:
    """A wire sequence
    `TEXT_MESSAGE_START -> TOOL_CALL_START -> TOOL_CALL_ARGS ->
     TOOL_CALL_END -> TOOL_CALL_RESULT -> TEXT_MESSAGE_END -> RUN_FINISHED`
    yields exactly one persisted tool_calls row with state='done' and a
    parent_message_id FK that resolves to the assistant Message row the
    persister lazily inserted on TEXT_MESSAGE_START."""
    from src.api.stream import _persist_events
    from src.streaming.event_bus import event_bus
    from src.streaming.execution_registry import (
        Execution,
        execution_registry,
    )

    factory = async_sessionmaker(fresh_engine, expire_on_commit=False)
    sid = await _seed_session(factory)
    session_id = str(sid)
    run_id = "run-tc-1"
    assistant_msg_id = f"assistant:run:{run_id}"
    tool_call_id = "tc-abc"

    # Drive the persister with a fresh execution + bus subscription.
    execution = Execution(session_id=session_id, run_id=run_id)
    async with execution_registry._lock:
        execution_registry._executions[(session_id, run_id)] = execution

    task = asyncio.create_task(
        _persist_events(
            session_id=session_id,
            session_uuid=sid,
            run_id=run_id,
            execution=execution,
        )
    )

    try:
        # Give the subscriber a chance to register on the bus before
        # we publish.
        await asyncio.sleep(0.05)

        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TEXT_MESSAGE_START",
                "messageId": assistant_msg_id,
                "role": "assistant",
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TOOL_CALL_START",
                "toolCallId": tool_call_id,
                "toolCallName": "kloc_search_code",
                "parentMessageId": assistant_msg_id,
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TOOL_CALL_ARGS",
                "toolCallId": tool_call_id,
                "delta": '{"q":"JWT"}',
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TOOL_CALL_END",
                "toolCallId": tool_call_id,
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TOOL_CALL_RESULT",
                "toolCallId": tool_call_id,
                "content": "found 3 hits",
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TEXT_MESSAGE_END",
                "messageId": assistant_msg_id,
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {"type": "RUN_FINISHED", "runId": run_id},
        )

        await asyncio.wait_for(task, timeout=5.0)
    finally:
        if not task.done():
            task.cancel()
        async with execution_registry._lock:
            execution_registry._executions.pop((session_id, run_id), None)
        await event_bus.close(session_id, run_id)

    async with factory() as session:
        rows = (
            await session.execute(
                select(ToolCall).where(ToolCall.session_id == sid)
            )
        ).scalars().all()
        assert len(rows) == 1, f"expected 1 tool_calls row, got {len(rows)}"
        row = rows[0]
        assert row.state == "done"
        assert row.tool_call_id == tool_call_id
        assert row.tool_name == "kloc_search_code"
        assert row.args == '{"q":"JWT"}'
        assert row.result == "found 3 hits"
        assert row.finalized_at is not None

        # parent_message_id FK resolves to the assistant Message row.
        msg = (
            await session.execute(
                select(Message).where(Message.id == row.parent_message_id)
            )
        ).scalar_one()
        assert msg.role == "assistant"
        assert msg.session_id == sid


async def test_persist_events_writes_denied_row_with_reason_and_hint(
    fresh_engine,
) -> None:
    """`CUSTOM { name: ToolCallDenied }` flips the row to `state='denied'`
    with `denied_reason` and `denied_hint` populated, even when the
    original `TOOL_CALL_START` ran and was later denied by policy."""
    from src.api.stream import _persist_events
    from src.streaming.event_bus import event_bus
    from src.streaming.execution_registry import (
        Execution,
        execution_registry,
    )

    factory = async_sessionmaker(fresh_engine, expire_on_commit=False)
    sid = await _seed_session(factory)
    session_id = str(sid)
    run_id = "run-tc-deny"
    assistant_msg_id = f"assistant:run:{run_id}"
    tool_call_id = "tc-denied"

    execution = Execution(session_id=session_id, run_id=run_id)
    async with execution_registry._lock:
        execution_registry._executions[(session_id, run_id)] = execution

    task = asyncio.create_task(
        _persist_events(
            session_id=session_id,
            session_uuid=sid,
            run_id=run_id,
            execution=execution,
        )
    )
    try:
        await asyncio.sleep(0.05)
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TEXT_MESSAGE_START",
                "messageId": assistant_msg_id,
                "role": "assistant",
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "TOOL_CALL_START",
                "toolCallId": tool_call_id,
                "toolCallName": "read_file",
                "parentMessageId": assistant_msg_id,
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {
                "type": "CUSTOM",
                "name": "ToolCallDenied",
                "value": {
                    "toolCallId": tool_call_id,
                    "toolName": "read_file",
                    "reason": "tool_limit:file_read",
                    "hint": "use start_line/end_line",
                    "parentMessageId": assistant_msg_id,
                },
            },
        )
        await event_bus.publish(
            session_id,
            run_id,
            {"type": "RUN_FINISHED", "runId": run_id},
        )

        await asyncio.wait_for(task, timeout=5.0)
    finally:
        if not task.done():
            task.cancel()
        async with execution_registry._lock:
            execution_registry._executions.pop((session_id, run_id), None)
        await event_bus.close(session_id, run_id)

    async with factory() as session:
        rows = (
            await session.execute(
                select(ToolCall).where(ToolCall.session_id == sid)
            )
        ).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.state == "denied"
        assert row.denied_reason == "tool_limit:file_read"
        assert row.denied_hint == "use start_line/end_line"
        assert row.finalized_at is not None
