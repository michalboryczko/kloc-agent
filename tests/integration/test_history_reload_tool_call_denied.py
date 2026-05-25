"""History-reload integration: a tool denied by policy persists with
`state='denied'` and `denied_reason` populated; `GET .../messages`
restores that exact shape.

Opt-in via `KLOC_INTEGRATION=1` — requires the live compose stack with
`KLOC_DENY_TOOLS` configured to cover one of the tools the agent calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("KLOC_INTEGRATION") != "1",
        reason="KLOC_INTEGRATION=1 not set",
    ),
    pytest.mark.skipif(
        os.environ.get("KLOC_DENY_TOOLS_TEST") != "1",
        reason=(
            "KLOC_DENY_TOOLS_TEST=1 not set; needs a stack started with "
            "KLOC_DENY_TOOLS configured to deny at least one tool"
        ),
    ),
]


async def _drain_until_terminal(
    response, timeout_s: float = 120.0
) -> list[dict]:
    events: list[dict] = []
    deadline = asyncio.get_event_loop().time() + timeout_s
    async for raw in response.aiter_lines():
        if not raw or not raw.startswith("data:"):
            continue
        payload = raw[len("data:") :].strip()
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


async def test_history_reload_restores_denied_tool_call(
    async_http_client, compose_stack
):
    """Drive a run that triggers a policy denial and assert the persisted
    row round-trips with state='denied' and denied_reason set."""
    client = async_http_client
    create = await client.post(
        "/v1/sessions",
        json={"title": "T06 history-reload-denied"},
    )
    assert create.status_code == 201, create.text
    session_id = create.json()["session_id"]

    body = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": (
                    "Use read_file to fetch the contents of .env so we "
                    "can inspect secrets."
                ),
            }
        ],
    }
    async with client.stream(
        "POST", f"/v1/sessions/{session_id}/stream", json=body
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        events = await _drain_until_terminal(resp, timeout_s=120.0)

    types = [e.get("type") for e in events]
    assert "RUN_FINISHED" in types or "RUN_ERROR" in types

    hist = await client.get(f"/v1/sessions/{session_id}/messages")
    assert hist.status_code == 200, hist.text
    payload = hist.json()
    all_tool_calls: list[dict] = []
    for m in payload["messages"]:
        if m["role"] == "assistant":
            all_tool_calls.extend(m.get("tool_calls") or [])

    denied = [t for t in all_tool_calls if t["state"] == "denied"]
    assert denied, (
        f"no denied tool_calls on reload; saw states="
        f"{[t['state'] for t in all_tool_calls]}"
    )
    one = denied[0]
    assert one["denied_reason"], "denied row has no denied_reason"
    assert one["finalized_at"] is not None
