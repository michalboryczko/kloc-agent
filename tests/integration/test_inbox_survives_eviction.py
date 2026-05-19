"""Regression test: a user_message pending in PGMQ survives a runner
eviction. The next runner that attaches to the same Session drains
the still-pending message.

This is the exact reproduction of the reported "second message does
nothing" bug. Before the PGMQ migration the inbox was an
`asyncio.Queue` tied to the per-spawn `RegistryEntry`; warm-idle
eviction dropped the in-memory queue and the next runner attached to
a fresh, empty one. With PGMQ the queue identity is keyed by
`session_id` alone, so a new runner reads from the same rows.
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
        _text(
            "SELECT queue_length FROM pgmq.metrics(:q)"
        ),
        {"q": queue},
    )
    row = result.first()
    return 0 if row is None else int(row[0])


@pytest.mark.integration
async def test_pending_message_survives_runner_eviction(
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
        warm_idle_s=60.0,
        heartbeat_timeout_s=30.0,
    )

    try:
        first_entry = await registry.get_or_spawn(
            session_id=session_id,
            hydration_payload={"session_id": session_id},
        )
        assert first_entry is not None

        run_id_two = str(uuid.uuid4())
        await send_user_message(
            conn,
            session_id,
            run_id_two,
            [{"role": "user", "content": "second turn"}],
        )
        await db_session.commit()

        assert await _pending_count(conn, queue) == 1

        await first_entry.warm_idle._on_evict()  # type: ignore[attr-defined]

        assert await _pending_count(conn, queue) == 1, (
            "eviction MUST NOT touch the durable PGMQ inbox; the "
            "second message must still be pending for the next runner"
        )

        second_entry = await registry.get_or_spawn(
            session_id=session_id,
            hydration_payload={"session_id": session_id},
        )
        assert second_entry is not None
        assert getattr(second_entry.handle, "runner_id", None) != getattr(
            first_entry.handle, "runner_id", None
        ), "fresh spawn must produce a new runner_id"

        async def _read_one() -> tuple[int, dict]:
            agen = inbox_consumer.consume_inbox(
                pg_dsn=pg_dsn,
                session_id=session_id,
                queue_name=queue,
            )
            try:
                return await asyncio.wait_for(
                    agen.__anext__(), timeout=10.0
                )
            finally:
                await agen.aclose()

        pickup_start = time.monotonic()
        msg_id, payload = await _read_one()
        pickup_latency_s = time.monotonic() - pickup_start
        assert pickup_latency_s <= 1.0, (
            f"pickup-to-first-event latency {pickup_latency_s:.3f}s "
            f"exceeds 1.0s bound"
        )
        assert payload["type"] == "user_message"
        assert payload["run_id"] == run_id_two
        assert payload["messages"] == [
            {"role": "user", "content": "second turn"}
        ]
        await inbox_consumer.delete_message(pg_dsn, queue, msg_id)
        assert await _pending_count(conn, queue) == 0
    finally:
        await registry.shutdown_all()
        await drop_inbox_queue(conn, session_id)
        await db_session.commit()
