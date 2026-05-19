"""PGMQ queue helpers for the runner inbox transport.

Per-session queue stored as Postgres rows. The backend produces via
`pgmq.send` paired with `NOTIFY` in the same transaction; the runner
consumes via `LISTEN` + `pgmq.read` + `pgmq.delete` (ack).

Queue names are valid SQL identifiers: `inbox_{session_id_no_dashes}`.
UUID dashes are stripped because PGMQ creates per-queue tables named
`pgmq.q_{queue_name}` and SQL identifiers may not contain hyphens.

All helpers accept a SQLAlchemy `AsyncConnection`. The boot lifespan
opens one via `engine.begin()`; the producer path passes
`await session.connection()` so `NOTIFY` participates in the same
transaction as `pgmq.send`.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import text


# PGMQ queue names must be valid SQL identifiers. Strip UUID dashes and
# reject anything else that would break `NOTIFY {queue}`.
_VALID_QUEUE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def inbox_queue_name(session_id: str) -> str:
    """Map a session UUID to its PGMQ queue name.

    Deterministic — the same `session_id` always produces the same name,
    so a new runner attaching to an existing session reads from the same
    queue the prior runner was reading from.
    """
    name = f"inbox_{session_id.replace('-', '')}"
    if not _VALID_QUEUE_NAME.match(name):
        raise ValueError(
            f"invalid session_id {session_id!r}: derived queue name "
            f"{name!r} is not a valid SQL identifier"
        )
    return name


async def ensure_extension(conn: Any) -> None:
    """Idempotent `CREATE EXTENSION IF NOT EXISTS pgmq`."""
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgmq"))


async def ensure_inbox_queue(conn: Any, session_id: str) -> str:
    """Create the per-session inbox queue if missing; return its name.

    `pgmq.create` is sequentially idempotent — running it twice returns
    success with NOTICEs — but its internal `CREATE TABLE`/`CREATE
    SEQUENCE` calls race under concurrent transactions for the same
    queue (two concurrent `POST /stream` for one session both call this).
    Serialize with a per-queue advisory transaction lock so the second
    caller waits for the first to finish creating the underlying
    relations; the lock releases automatically at transaction end.
    """
    queue = inbox_queue_name(session_id)
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:q))"),
        {"q": queue},
    )
    await conn.execute(
        text("SELECT pgmq.create(:q)"), {"q": queue}
    )
    return queue


async def send_user_message(
    conn: Any,
    session_id: str,
    run_id: str,
    messages: list[dict[str, Any]],
) -> int:
    """Enqueue a user_message payload and wake the runner via NOTIFY.

    NOTIFY MUST share a transaction with `pgmq.send` so the runner's
    LISTEN is signalled only after the queue row is durably committed.
    Caller is responsible for committing the surrounding transaction.
    """
    queue = inbox_queue_name(session_id)
    payload = {
        "type": "user_message",
        "run_id": run_id,
        "messages": messages,
    }
    result = await conn.execute(
        text("SELECT pgmq.send(:q, CAST(:p AS jsonb))"),
        {"q": queue, "p": json.dumps(payload)},
    )
    row = result.first()
    msg_id = 0 if row is None else int(row[0])

    # NOTIFY does not accept bind parameters; the channel must be a
    # literal identifier. `queue` is already validated as a SQL
    # identifier in `inbox_queue_name`, so interpolating it here is safe.
    await conn.execute(text(f"NOTIFY {queue}"))
    return msg_id


async def drop_inbox_queue(conn: Any, session_id: str) -> None:
    """Drop the per-session queue on explicit session close.

    Not called on warm-idle eviction — pending messages must survive a
    runner respawn for the same session.
    """
    queue = inbox_queue_name(session_id)
    await conn.execute(
        text("SELECT pgmq.drop_queue(:q)"), {"q": queue}
    )
