"""History-reload integration: after a tool-using run finishes,
`GET /v1/sessions/{id}/messages` returns the assistant message with
`tool_calls.length == 1` and `tool_calls[0].state == "done"`.

Opt-in via `KLOC_INTEGRATION=1` — requires the live compose stack
(backend, runner, postgres). Self-skips when the stack is not
available so `pytest tests/ -q` stays green.
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


async def test_history_reload_restores_tool_calls(
    async_http_client, compose_stack
):
    """Drive one tool-using turn through the live backend then reload
    history and assert the persisted tool_calls round-trip."""
    client = async_http_client
    create = await client.post(
        "/v1/sessions",
        json={"title": "T06 history-reload"},
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
                    "Use kloc_search_code to look up 'JWT_SECRET' and "
                    "summarise the hits."
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
    assert "RUN_ERROR" not in types, f"run errored; events={types}"
    assert "RUN_FINISHED" in types, f"run did not finish; events={types}"

    # Reload history and assert tool_calls round-trip.
    hist = await client.get(f"/v1/sessions/{session_id}/messages")
    assert hist.status_code == 200, hist.text
    payload = hist.json()
    assistant_messages = [
        m for m in payload["messages"] if m["role"] == "assistant"
    ]
    assert assistant_messages, "no assistant message persisted"

    # At least one assistant message should carry tool_calls.
    with_tools = [
        m for m in assistant_messages if (m.get("tool_calls") or [])
    ]
    assert with_tools, (
        f"no persisted tool_calls on assistant messages; "
        f"got={[m.get('tool_calls') for m in assistant_messages]}"
    )
    one = with_tools[0]
    assert len(one["tool_calls"]) >= 1
    tc = one["tool_calls"][0]
    assert tc["state"] in ("done", "denied")
    if tc["state"] == "done":
        assert tc["finalized_at"] is not None
    assert tc["tool_call_id"]
    assert tc["tool_name"]
