"""E2E Scenario 5 — Backend cursor-replay (AC18 mode 1).

Per team-lead's scope nuance: test the BACKEND cursor-replay behavior
directly via HTTP, NOT via browser tab cycle. AC18-mode-2 (browser UI
replay across tab close) is out of PoC scope.

Test flow:
1. POST /v1/sessions/{id}/stream with Accept: text/event-stream.
2. Read N events via the sse_helpers client; capture the last `seq`.
3. Disconnect mid-stream (drop the stream context).
4. GET /v1/sessions/{id}/stream?run_id=<from-step-1>&last_event_id=<seq>
   → assert events replay from cursor + live tail continues to RUN_FINISHED.

Milestone gate: M3 → M4.
"""
from __future__ import annotations

import uuid

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.asyncio
async def test_scenario_05_backend_cursor_replay(
    llm_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
):
    """Disconnect mid-stream → GET with last_event_id replays + live-tails to RUN_FINISHED (AC18)."""
    from sqlalchemy import select

    from src.db.models import AuditLog, Message

    create = await async_http_client.post("/v1/sessions", json={})
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    run_id = str(uuid.uuid4())
    body = {
        "run_id": run_id,
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": (
                    "List 3 handlers from the indexed codebase using kloc tools "
                    "and explain each in one sentence."
                ),
            }
        ],
    }

    # Step 1-3: POST and consume events until we see the first TOOL_CALL_END
    # (a natural mid-run breakpoint). We stop the stream consumption then
    # capture the last `seq` we saw.
    captured: list[dict] = []
    last_seq: int | None = None
    async with sse_helpers.sse_request(
        async_http_client,
        "POST",
        f"/v1/sessions/{session_id}/stream",
        json=body,
        timeout=300.0,
    ) as stream:
        async for ev in stream:
            captured.append(ev)
            if "seq" in ev and isinstance(ev["seq"], int):
                last_seq = ev["seq"]
            if ev.get("type") == "TOOL_CALL_END":
                # Mid-run disconnect — drop the stream context.
                break

    assert last_seq is not None, (
        "no events carried a `seq` field; ExecutionRegistry cursor-replay "
        "depends on this — AC18 cannot be validated"
    )
    assert any(e.get("type") == "TOOL_CALL_END" for e in captured), (
        "did not observe TOOL_CALL_END before disconnect; agent likely "
        "produced no tool call (re-check prompt)"
    )

    # Step 4: GET with last_event_id; expect cursor-replay + live tail.
    resumed = await sse_helpers.collect_until(
        async_http_client,
        "GET",
        f"/v1/sessions/{session_id}/stream",
        params={"run_id": run_id, "last_event_id": last_seq},
        stop_types=("RUN_FINISHED", "RUN_ERROR"),
        timeout=300.0,
    )
    sse_helpers.assert_run_completed(resumed)

    # All events in `resumed` must have seq > last_seq (cursor-replay honoured).
    resumed_seqs = [e.get("seq") for e in resumed if isinstance(e.get("seq"), int)]
    assert resumed_seqs, "resumed stream had no events with `seq` field"
    assert min(resumed_seqs) > last_seq, (
        f"cursor-replay regression: resumed stream included seq <= cursor "
        f"({min(resumed_seqs)} <= {last_seq}); ExecutionRegistry skipped "
        f"the wrong slice"
    )

    # DB invariant: full run persisted despite the disconnect — assistant
    # message finalized, no orphan rows.
    db_session.expire_all()
    sid = uuid.UUID(session_id)
    msgs = list(
        (
            await db_session.execute(
                select(Message).where(Message.session_id == sid)
            )
        ).scalars()
    )
    asst = [m for m in msgs if m.role == "assistant"]
    assert asst, "no assistant message persisted"
    assert all(m.finalized_at is not None for m in asst), (
        "assistant messages must be finalized even when client dropped mid-stream"
    )

    # No stream_orphaned audit row for this session (the run completed; no
    # orphan recovery should have fired).
    audit_types = [
        r.event_type
        for r in (
            await db_session.execute(
                select(AuditLog).where(AuditLog.session_id == sid)
            )
        ).scalars()
    ]
    assert "stream_orphaned" not in audit_types, (
        f"resumed stream completed cleanly; stream_orphaned must NOT appear; "
        f"audit types: {sorted(set(audit_types))}"
    )
