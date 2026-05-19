"""PGMQ inbox consumer for the runner.

Holds one long-lived asyncpg connection per session that does:
  * `LISTEN inbox_{slug}` for sub-millisecond wake-up on new user messages
  * `pgmq.read(queue, vt=VT_SECONDS, qty=1)` to fetch one message
  * a 30 s fallback poll so a missed `NOTIFY` across reconnects cannot
    strand the runner

`consume_inbox` is an async iterator yielding `(msg_id, payload)` pairs;
the caller MUST ack with `delete_message(msg_id)` only after the message
has been fully processed. Failure-without-ack causes PGMQ to redeliver
the message after `VT_SECONDS` — the survives-eviction guarantee.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

import asyncpg

log = logging.getLogger(__name__)

VT_SECONDS = 300
FALLBACK_POLL_S = 30.0
RECONNECT_BACKOFF_S = 1.0


async def consume_inbox(
    pg_dsn: str,
    session_id: str,
    queue_name: str,
) -> AsyncIterator[tuple[int, dict]]:
    """Yield `(msg_id, payload)` tuples in FIFO order from the session's
    PGMQ inbox queue.

    Caller acks each message by awaiting `delete_message(pg_dsn,
    queue_name, msg_id)` AFTER processing. An un-acked message becomes
    visible to the next consumer after `VT_SECONDS` — that is the
    redelivery mechanism the survives-eviction guarantee rests on.
    """
    del session_id  # logging/observability hook; queue_name is the wire key

    while True:
        conn: asyncpg.Connection | None = None
        try:
            conn = await asyncpg.connect(pg_dsn)
            wake = asyncio.Event()

            def _on_notify(*_args: object) -> None:
                wake.set()

            await conn.add_listener(queue_name, _on_notify)

            while True:
                row = await conn.fetchrow(
                    "SELECT msg_id, message::text AS message "
                    "FROM pgmq.read($1, $2, $3)",
                    queue_name,
                    VT_SECONDS,
                    1,
                )
                if row is not None:
                    payload = json.loads(row["message"])
                    yield int(row["msg_id"]), payload
                    continue

                wake.clear()
                try:
                    await asyncio.wait_for(
                        wake.wait(), timeout=FALLBACK_POLL_S
                    )
                except asyncio.TimeoutError:
                    pass
        except (asyncpg.PostgresConnectionError, OSError):
            log.exception("inbox_consumer.connection_lost")
            await asyncio.sleep(RECONNECT_BACKOFF_S)
        finally:
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    log.exception("inbox_consumer.close_failed")


async def delete_message(
    pg_dsn: str,
    queue_name: str,
    msg_id: int,
) -> None:
    """Acknowledge one inbox message so PGMQ does not redeliver it
    after the visibility timeout."""
    conn = await asyncpg.connect(pg_dsn)
    try:
        await conn.fetchrow(
            "SELECT pgmq.delete($1, $2)", queue_name, msg_id
        )
    finally:
        await conn.close()
