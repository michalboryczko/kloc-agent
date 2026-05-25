"""End-to-end: a 24 MiB tool result is offloaded to S3 and surfaces on
the AG-UI stream as a `ToolCallResult` plus an `ArtifactAttached`
CUSTOM event, with no `runner_heartbeat_lost` and no HTTP 413.

Self-skips when the compose stack is not available so
`pytest tests/ -q` stays green without docker. Set
`KLOC_INTEGRATION=1` and bring up the stack (with a fixture MCP
server providing a `read_huge` tool) to run this.
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
        os.environ.get("KLOC_FIXTURE_HUGE_TOOL") != "1",
        reason="fixture MCP server with `read_huge` tool not configured",
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


async def test_24mib_tool_result_offloads_to_artifact(
    async_http_client, db_session, compose_stack
):
    pytest.importorskip("sqlalchemy")
    from sqlalchemy import text

    client = async_http_client
    create_resp = await client.post(
        "/v1/sessions",
        json={"title": "huge-tool-result regression"},
    )
    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]

    body = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Call read_huge and summarise the output.",
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

    tool_results = [e for e in events if e.get("type") == "TOOL_CALL_RESULT"]
    assert tool_results, "agent did not surface any TOOL_CALL_RESULT"
    artifact_refs = [
        e for e in tool_results
        if isinstance(e.get("value"), dict)
        and isinstance(e["value"].get("content"), dict)
        and e["value"]["content"].get("artifactId")
    ]
    assert artifact_refs, (
        "expected at least one TOOL_CALL_RESULT to reference an artifactId; "
        f"got value-shapes={[e.get('value') for e in tool_results]}"
    )

    custom_events = [e for e in events if e.get("type") == "CUSTOM"]
    attached = [c for c in custom_events if c.get("name") == "ArtifactAttached"]
    assert attached, "no ArtifactAttached CUSTOM event surfaced"

    # No frame-rejected audit; runner should not have died.
    audit_rows = await db_session.execute(
        text(
            "SELECT event_type FROM audit_log "
            "WHERE session_id = :sid "
            "AND event_type IN ('runner_heartbeat_lost', "
            "'runner_channel_frame_rejected')"
        ),
        {"sid": session_id},
    )
    bad = [r[0] for r in audit_rows.all()]
    assert "runner_heartbeat_lost" not in bad, (
        f"runner died mid-run; audit rows={bad}"
    )
    assert "runner_channel_frame_rejected" not in bad, (
        f"backend rejected a frame; audit rows={bad}"
    )
