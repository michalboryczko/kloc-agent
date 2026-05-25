"""Cold-start runner survives a 25 s injected init delay.

The runner image reads `INJECT_RUNNER_INIT_DELAY_S` once at the top of
`runner/__main__.main()` and sleeps that many seconds before any other
work happens — simulating a slow MCP-init handshake. With T03 in place
(`runner_ready` resets the watcher, the runner emits its first explicit
heartbeat at t≈0, and the default `runner_heartbeat_timeout_s` is 60 s),
the spawn-to-first-beat budget covers the 25 s init plus everything
that follows; no `runner_heartbeat_lost` row appears for the session.

Opt-in: requires `KLOC_INTEGRATION=1` and a live docker-compose stack.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("KLOC_INTEGRATION") != "1",
        reason="KLOC_INTEGRATION=1 not set",
    ),
]


async def _drain_until_terminal(stream) -> str | None:
    """Consume SSE lines until `RUN_FINISHED` or `RUN_ERROR`. Returns
    the terminal frame type seen, or `None` on stream close without one."""
    async for line in stream.aiter_lines():
        if not line:
            continue
        if '"type":"RUN_FINISHED"' in line:
            return "RUN_FINISHED"
        if '"type":"RUN_ERROR"' in line:
            return "RUN_ERROR"
    return None


async def test_cold_start_runner_survives_injected_delay(
    async_http_client, db_session, compose_stack
):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import text

    client = async_http_client

    create_resp = await client.post(
        "/v1/sessions",
        json={"title": "cold-start regression"},
    )
    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]

    body = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {"id": str(uuid.uuid4()), "role": "user", "content": "hello"}
        ],
    }

    terminal: str | None = None
    async with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/stream",
        json=body,
        timeout=120.0,
    ) as response:
        assert response.status_code == 200, await response.aread()
        try:
            terminal = await asyncio.wait_for(
                _drain_until_terminal(response), timeout=90.0
            )
        except asyncio.TimeoutError:
            pytest.fail(
                "no terminal frame received within 90 s; cold-start "
                "runner likely died before producing any output"
            )

    assert terminal == "RUN_FINISHED", (
        f"expected RUN_FINISHED, got {terminal!r}"
    )

    heartbeat_lost = await db_session.execute(
        text(
            "SELECT count(*) FROM audit_log "
            "WHERE session_id = :sid AND event_type = 'runner_heartbeat_lost'"
        ),
        {"sid": session_id},
    )
    assert heartbeat_lost.scalar_one() == 0, (
        "runner_heartbeat_lost must not appear for a cold-start session "
        "covered by the 60 s budget"
    )

    persisted = await db_session.execute(
        text(
            "SELECT count(*) FROM audit_log "
            "WHERE session_id = :sid AND event_type = 'message_persisted'"
        ),
        {"sid": session_id},
    )
    assert persisted.scalar_one() >= 2, (
        "expected at least one user + one assistant message_persisted row"
    )
