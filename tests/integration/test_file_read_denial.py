"""End-to-end: file_read against a 5 MiB fixture is denied with a hint.

Self-skips when the compose stack is not available so
`pytest tests/ -q` stays green without docker. Set
`KLOC_INTEGRATION=1` and bring up the stack (with the fixture stat
server reachable at `KLOC_INTELLIGENCE_STAT_URL`) to run this.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("KLOC_INTEGRATION") != "1",
        reason="KLOC_INTEGRATION=1 not set",
    ),
    pytest.mark.skipif(
        os.environ.get("KLOC_FIXTURE_STAT_SERVER") != "1",
        reason="fixture stat server (tests/fixtures/intel_stat_server.py) not configured",
    ),
]


async def _collect_until_terminal(
    response, timeout_s: float = 60.0
) -> list[dict]:
    events: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout_s
    async for raw in response.aiter_lines():
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[len("data:"):].strip()
        if not payload:
            continue
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError:
            continue
        events.append(frame)
        if frame.get("type") in ("RUN_FINISHED", "RUN_ERROR"):
            break
        if asyncio.get_event_loop().time() > deadline:
            break
    return events


async def test_file_read_over_cap_is_denied_with_hint(
    async_http_client, db_session, compose_stack
) -> None:
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import text

    client = async_http_client
    create_resp = await client.post(
        "/v1/sessions",
        json={"title": "file_read denial regression"},
    )
    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]

    body = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Read /workspace/vendor/big.json and summarise it.",
            }
        ],
    }
    async with client.stream(
        "POST", f"/v1/sessions/{session_id}/stream", json=body
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        events = await _collect_until_terminal(resp, timeout_s=120.0)

    types = [e.get("type") for e in events]
    assert "RUN_ERROR" not in types, f"run errored; events={types}"

    custom_events = [e for e in events if e.get("type") == "CUSTOM"]
    denied = [c for c in custom_events if c.get("name") == "ToolCallDenied"]
    assert denied, f"no ToolCallDenied event; saw types={types}"
    first = denied[0]
    assert first["value"]["reason"] == "tool_limit:file_too_large"
    hint = first["value"].get("hint", "")
    assert "MiB" in hint
    assert "start_line" in hint

    audit_rows = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE session_id = :sid "
            "AND event_type = 'tool_call.denied' "
            "AND payload->'decision'->>'reason' = 'tool_limit:file_too_large'"
        ),
        {"sid": session_id},
    )
    deny_count = audit_rows.scalar() or 0
    assert deny_count >= 1, "expected at least one tool_call.denied audit row"
