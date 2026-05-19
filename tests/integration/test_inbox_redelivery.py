"""Regression test: an unacked PGMQ inbox message is redelivered to a
fresh consumer after the visibility timeout expires.

This is the property that lets a runner's session survive eviction —
if the previous runner crashed or was killed before acking, the next
runner picks up the message.

The production `VT_SECONDS=300` would make the test wait > 5 min, so we
monkeypatch it to a small value. The production constant stays hardcoded
per the implementation plan; only the test patches it.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

from sqlalchemy.exc import DBAPIError

from runner import inbox_consumer
from src.messaging.pgmq import (
    drop_inbox_queue,
    ensure_extension,
    ensure_inbox_queue,
    send_user_message,
)


def _pg_dsn_for_asyncpg() -> str:
    """Convert the SQLAlchemy asyncpg URL to the raw asyncpg form."""
    url = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent"
    )
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _ensure_pgmq_or_skip(conn) -> None:
    try:
        await ensure_extension(conn)
    except DBAPIError as exc:
        if "pgmq" in str(exc).lower():
            pytest.skip(f"pgmq extension unavailable on test Postgres: {exc}")
        raise


@pytest.mark.integration
async def test_unacked_message_is_redelivered_to_next_consumer(
    db_session, monkeypatch
) -> None:
    short_vt = 2
    monkeypatch.setattr(inbox_consumer, "VT_SECONDS", short_vt)
    monkeypatch.setattr(inbox_consumer, "FALLBACK_POLL_S", 1.0)

    conn = await db_session.connection()
    await _ensure_pgmq_or_skip(conn)
    session_id = str(uuid.uuid4())
    queue = await ensure_inbox_queue(conn, session_id)
    pg_dsn = _pg_dsn_for_asyncpg()

    try:
        run_id = str(uuid.uuid4())
        messages = [{"role": "user", "content": "first try"}]
        await send_user_message(conn, session_id, run_id, messages)
        await db_session.commit()

        async def _read_one_without_ack() -> tuple[int, dict]:
            agen = inbox_consumer.consume_inbox(
                pg_dsn=pg_dsn,
                session_id=session_id,
                queue_name=queue,
            )
            try:
                msg_id, payload = await asyncio.wait_for(
                    agen.__anext__(), timeout=10.0
                )
            finally:
                await agen.aclose()
            return msg_id, payload

        first_msg_id, first_payload = await _read_one_without_ack()
        assert first_payload["type"] == "user_message"
        assert first_payload["run_id"] == run_id
        assert first_payload["messages"] == messages

        await asyncio.sleep(short_vt + 1)

        second_msg_id, second_payload = await _read_one_without_ack()
        assert second_msg_id == first_msg_id
        assert second_payload == first_payload

        await inbox_consumer.delete_message(pg_dsn, queue, second_msg_id)
    finally:
        cleanup_conn = await db_session.connection()
        await drop_inbox_queue(cleanup_conn, session_id)
        await db_session.commit()
