"""Regression test (e2e variant): the pending PGMQ inbox message also
survives when eviction is driven by the real warm-idle timer, not the
direct `_on_evict` callback.

The fast integration version (`tests/integration/test_inbox_survives_eviction.py`)
exercises the same eviction code path by calling the callback directly.
This e2e variant additionally proves the production rescue path: warm
idle timer fires -> `_on_evict` fires -> the still-pending PGMQ row is
drained by the next runner. Marked `e2e` so CI can opt in.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest
from sqlalchemy import text as _text
from sqlalchemy.exc import DBAPIError

from runner import inbox_consumer
from src.messaging.pgmq import (
    drop_inbox_queue,
    ensure_extension,
    ensure_inbox_queue,
    inbox_queue_name,
    send_user_message,
)


pytestmark = [pytest.mark.e2e]


WARM_IDLE_S = 2.0


def _pg_dsn_for_asyncpg() -> str:
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


async def _pending_count(conn, queue: str) -> int:
    result = await conn.execute(
        _text("SELECT queue_length FROM pgmq.metrics(:q)"),
        {"q": queue},
    )
    row = result.first()
    return 0 if row is None else int(row[0])


async def test_pending_message_survives_warm_idle_timer_eviction(
    db_session, mock_runner, monkeypatch
) -> None:
    monkeypatch.setattr(inbox_consumer, "VT_SECONDS", 5)
    monkeypatch.setattr(inbox_consumer, "FALLBACK_POLL_S", 1.0)

    conn = await db_session.connection()
    await _ensure_pgmq_or_skip(conn)

    from src.runner_mgmt.registry import RunnerRegistry

    session_id = str(uuid.uuid4())
    queue = await ensure_inbox_queue(conn, session_id)
    assert queue == inbox_queue_name(session_id)
    pg_dsn = _pg_dsn_for_asyncpg()

    registry = RunnerRegistry(
        runner=mock_runner,  # type: ignore[arg-type]
        warm_idle_s=WARM_IDLE_S,
        heartbeat_timeout_s=30.0,
    )

    try:
        first_entry = await registry.get_or_spawn(
            session_id=session_id,
            hydration_payload={"session_id": session_id},
        )
        assert first_entry is not None
        first_runner_id = getattr(first_entry.handle, "runner_id", None)

        run_id = str(uuid.uuid4())
        await send_user_message(
            conn,
            session_id,
            run_id,
            [{"role": "user", "content": "pending across timer eviction"}],
        )
        await db_session.commit()
        assert await _pending_count(conn, queue) == 1

        first_entry.warm_idle.on_run_finished()
        await asyncio.sleep(WARM_IDLE_S + 1.5)
        await first_entry.warm_idle.await_kill_in_flight()

        assert await registry.get(session_id) is None, (
            "warm-idle timer should have evicted the first runner"
        )
        assert await _pending_count(conn, queue) == 1, (
            "warm-idle timer eviction MUST NOT drain the durable PGMQ inbox"
        )

        second_entry = await registry.get_or_spawn(
            session_id=session_id,
            hydration_payload={"session_id": session_id},
        )
        assert second_entry is not None
        assert getattr(second_entry.handle, "runner_id", None) != first_runner_id

        agen = inbox_consumer.consume_inbox(
            pg_dsn=pg_dsn,
            session_id=session_id,
            queue_name=queue,
        )
        try:
            pickup_start = time.monotonic()
            msg_id, payload = await asyncio.wait_for(
                agen.__anext__(), timeout=10.0
            )
            pickup_latency_s = time.monotonic() - pickup_start
        finally:
            await agen.aclose()

        assert pickup_latency_s <= 1.0, (
            f"pickup-to-first-event latency {pickup_latency_s:.3f}s "
            f"exceeds AC1 bound of 1.0s"
        )
        assert payload["type"] == "user_message"
        assert payload["run_id"] == run_id
        await inbox_consumer.delete_message(pg_dsn, queue, msg_id)
        assert await _pending_count(conn, queue) == 0
    finally:
        await registry.shutdown_all()
        await drop_inbox_queue(conn, session_id)
        await db_session.commit()
