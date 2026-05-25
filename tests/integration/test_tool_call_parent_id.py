"""End-to-end: a session that fires tool calls surfaces `TOOL_CALL_START`
AG-UI events whose `parentMessageId` is turn-stable — all tool calls in
a single run share one parent id, AND the eventual assistant prose
(`TEXT_MESSAGE_START`) carries the SAME id so the FE reducer groups
tool cards and prose into one bubble.

Self-skips when the compose stack is not available so
`pytest tests/ -q` stays green without docker. Set `KLOC_INTEGRATION=1`
and bring up the stack to run this.
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
]


async def _collect_until_terminal(
    response, timeout_s: float = 120.0
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


async def test_tool_call_parent_id_is_turn_stable_and_matches_text_start(
    async_http_client, compose_stack
):
    """The regression detector for T05.

    A multi-tool-call turn produces events that satisfy three invariants:

      1. There is at least one `TOOL_CALL_START`.
      2. Every `TOOL_CALL_START.parentMessageId` in the same `runId`
         resolves to the SAME string. Heterogeneous parent ids — what
         we observed before T05 — means each tool call lands in its
         own FE bubble.
      3. If a `TEXT_MESSAGE_START` is emitted (the model spoke at all),
         its `messageId` MUST equal that shared parent id. Otherwise
         the tool cards group in bubble A and the prose lands in
         bubble B.

    If the runner-side parent linking regresses (e.g. someone reverts
    the AG-UI wrap or stops emitting the synthetic TEXT_MESSAGE_START at
    RUN_STARTED), at least one of these invariants fails and this test
    catches it.
    """
    client = async_http_client
    create_resp = await client.post(
        "/v1/sessions",
        json={"title": "T05 parent linking"},
    )
    assert create_resp.status_code == 201, create_resp.text
    session_id = create_resp.json()["session_id"]

    body = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": (
                    "Use the file_read tool to read "
                    "`/app/pyproject.toml` from the runner container, "
                    "then use file_read again to read "
                    "`/app/runner/Dockerfile`. After both reads, "
                    "briefly summarise."
                ),
            }
        ],
    }
    async with client.stream(
        "POST", f"/v1/sessions/{session_id}/stream", json=body
    ) as resp:
        assert resp.status_code == 200, await resp.aread()
        events = await _collect_until_terminal(resp, timeout_s=180.0)

    types = [e.get("type") for e in events]
    assert "RUN_ERROR" not in types, f"run errored; events={types}"

    tool_starts = [e for e in events if e.get("type") == "TOOL_CALL_START"]
    text_starts = [e for e in events if e.get("type") == "TEXT_MESSAGE_START"]

    assert tool_starts, (
        "expected at least one TOOL_CALL_START in the run; "
        f"event types={types}"
    )

    # Invariant 1: every TOOL_CALL_START carries a non-null parentMessageId.
    for ts in tool_starts:
        assert ts.get("parentMessageId"), (
            f"TOOL_CALL_START missing parentMessageId: {ts}"
        )

    # Invariant 2: all TOOL_CALL_START.parentMessageId values within a
    # single run are equal — the turn-stable id.
    parent_ids = {ts["parentMessageId"] for ts in tool_starts}
    assert len(parent_ids) == 1, (
        "all TOOL_CALL_START events in one run must share one "
        f"parentMessageId; got {len(parent_ids)} distinct ids: "
        f"{sorted(parent_ids)}"
    )
    turn_id = next(iter(parent_ids))

    # Invariant 3: at least one TEXT_MESSAGE_START fires (we synthesise
    # one at RUN_STARTED), and its messageId matches the turn id so the
    # FE reducer attaches prose to the same bubble as the tool cards.
    assert text_starts, (
        "expected at least one TEXT_MESSAGE_START in the run — the "
        "runner must synthesise one at RUN_STARTED so tool cards and "
        "prose share a parent. Got types="
        f"{types}"
    )
    text_start_ids = {ts.get("messageId") for ts in text_starts}
    assert turn_id in text_start_ids, (
        "no TEXT_MESSAGE_START.messageId matches the tool-call "
        f"parentMessageId={turn_id!r}; got text_starts="
        f"{sorted(i for i in text_start_ids if i)}"
    )
