"""Tests for the PGMQ inbox-transport helpers.

Pure-Python tests of `inbox_queue_name` are marked `unit`; tests that
exercise `ensure_extension`, `ensure_inbox_queue`, `send_user_message`,
and `drop_inbox_queue` against a real pgmq-enabled Postgres are marked
`integration` and skip when Postgres is unreachable (via `db_session`).
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text as _text

from src.messaging.pgmq import (
    drop_inbox_queue,
    ensure_extension,
    ensure_inbox_queue,
    inbox_queue_name,
    send_user_message,
)


def _new_session_id() -> str:
    return str(uuid.uuid4())


@pytest.mark.unit
def test_inbox_queue_name_is_deterministic_and_strips_dashes() -> None:
    sid = "abcdef01-2345-6789-abcd-ef0123456789"
    name = inbox_queue_name(sid)
    assert name == "inbox_abcdef0123456789abcdef0123456789"
    assert inbox_queue_name(sid) == name


@pytest.mark.unit
def test_inbox_queue_name_is_a_valid_sql_identifier() -> None:
    sid = str(uuid.uuid4())
    name = inbox_queue_name(sid)
    assert name.startswith("inbox_")
    assert all(c.isalnum() or c == "_" for c in name)
    assert "-" not in name


@pytest.mark.unit
def test_inbox_queue_name_rejects_invalid_session_id() -> None:
    with pytest.raises(ValueError, match="invalid session_id"):
        inbox_queue_name("not a uuid; DROP TABLE foo;--")


@pytest.mark.integration
async def test_ensure_extension_is_idempotent(db_session) -> None:
    conn = await db_session.connection()
    await ensure_extension(conn)
    await ensure_extension(conn)
    result = await conn.execute(
        _text("SELECT 1 FROM pg_extension WHERE extname = 'pgmq'")
    )
    assert result.first() is not None


@pytest.mark.integration
async def test_ensure_inbox_queue_is_idempotent(db_session) -> None:
    conn = await db_session.connection()
    await ensure_extension(conn)
    sid = _new_session_id()
    queue = await ensure_inbox_queue(conn, sid)
    assert queue == inbox_queue_name(sid)
    queue_again = await ensure_inbox_queue(conn, sid)
    assert queue_again == queue
    await drop_inbox_queue(conn, sid)


@pytest.mark.integration
async def test_send_user_message_round_trip_via_pgmq_read(db_session) -> None:
    conn = await db_session.connection()
    await ensure_extension(conn)
    sid = _new_session_id()
    await ensure_inbox_queue(conn, sid)
    try:
        run_id = str(uuid.uuid4())
        messages = [{"role": "user", "content": "hello"}]
        msg_id = await send_user_message(conn, sid, run_id, messages)
        assert msg_id > 0

        result = await conn.execute(
            _text(
                "SELECT msg_id, message::text FROM pgmq.read(:q, 30, 1)"
            ),
            {"q": inbox_queue_name(sid)},
        )
        row = result.first()
        assert row is not None, "expected one message from pgmq.read"
        read_msg_id, raw_message = row
        assert int(read_msg_id) == msg_id
        payload = json.loads(raw_message)
        assert payload == {
            "type": "user_message",
            "run_id": run_id,
            "messages": messages,
        }
    finally:
        await drop_inbox_queue(conn, sid)


@pytest.mark.integration
async def test_drop_inbox_queue_removes_queue(db_session) -> None:
    conn = await db_session.connection()
    await ensure_extension(conn)
    sid = _new_session_id()
    await ensure_inbox_queue(conn, sid)

    listed = await conn.execute(
        _text(
            "SELECT queue_name FROM pgmq.list_queues() "
            "WHERE queue_name = :q"
        ),
        {"q": inbox_queue_name(sid)},
    )
    assert listed.first() is not None

    await drop_inbox_queue(conn, sid)
    listed_after = await conn.execute(
        _text(
            "SELECT queue_name FROM pgmq.list_queues() "
            "WHERE queue_name = :q"
        ),
        {"q": inbox_queue_name(sid)},
    )
    assert listed_after.first() is None
