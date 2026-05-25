"""Second-message-into-warm-runner regression test.

Drives a real PGMQ round-trip via the docker-compose stack. Self-skips
when the stack is not available so `pytest tests/ -q` stays green
without docker. Set `KLOC_INTEGRATION=1` (and ensure the compose stack
is up, with `BACKEND_URL` and `DATABASE_URL` set) to run this.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("KLOC_INTEGRATION") != "1",
        reason="KLOC_INTEGRATION=1 not set",
    ),
]


async def _wait_until_pgmq_send(
    conn: object, queue_slug: str, since_ts: float, deadline_s: float
) -> float | None:
    """Poll the PGMQ queue for the first message arriving after `since_ts`.

    PGMQ writes a `enqueued_at` column on `pgmq.q_<queue>`. Returns the
    wall-clock time at which the row appeared, or None on timeout.
    """
    from sqlalchemy import text  # type: ignore

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        result = await conn.execute(
            text(
                f"SELECT extract(epoch from enqueued_at) "
                f"FROM pgmq.q_{queue_slug} "
                f"WHERE extract(epoch from enqueued_at) > :since "
                f"ORDER BY enqueued_at ASC LIMIT 1"
            ),
            {"since": since_ts},
        )
        row = result.first()
        if row is not None:
            return float(row[0])
        await asyncio.sleep(0.05)
    return None


async def test_second_message_lands_in_pgmq_within_500ms(
    async_http_client, db_session, compose_stack
):
    """Turn 1 completes; turn 2 must enqueue into PGMQ within 500 ms
    of the POST."""
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import text

    # Best-effort PGMQ availability probe.
    try:
        await db_session.execute(text("SELECT 1 FROM pgmq.meta LIMIT 1"))
    except Exception:
        pytest.skip("PGMQ extension not installed in the connected database")

    client = async_http_client
    create_resp = await client.post(
        "/v1/sessions",
        json={"title": "warm-runner regression"},
    )
    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]

    # Turn 1: open the SSE stream and wait for RUN_FINISHED.
    body_turn1 = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {"id": str(uuid.uuid4()), "role": "user", "content": "hello"}
        ],
    }
    async with client.stream(
        "POST", f"/v1/sessions/{session_id}/stream", json=body_turn1
    ) as r1:
        assert r1.status_code == 200, await r1.aread()
        async for line in r1.aiter_lines():
            if '"type":"RUN_FINISHED"' in line or '"type":"RUN_ERROR"' in line:
                break

    # Turn 2: time how long it takes for the POST to enqueue into PGMQ.
    queue_slug = f"inbox_{session_id.replace('-', '')}"
    post_ts = time.time()
    body_turn2 = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {"id": str(uuid.uuid4()), "role": "user", "content": "follow up"}
        ],
    }
    post_task = asyncio.create_task(
        client.post(f"/v1/sessions/{session_id}/stream", json=body_turn2)
    )

    enqueue_ts = await _wait_until_pgmq_send(
        db_session, queue_slug, since_ts=post_ts, deadline_s=5.0
    )

    # Best-effort cleanup of the POST task — body may stream forever.
    post_task.cancel()
    try:
        await post_task
    except (asyncio.CancelledError, Exception):
        pass

    assert enqueue_ts is not None, (
        f"turn-2 message never reached pgmq.q_{queue_slug} within 5 s"
    )
    delta = enqueue_ts - post_ts
    assert delta < 0.5, (
        f"turn-2 enqueue took {delta * 1000:.0f} ms; "
        f"warm-runner self-block likely back"
    )
