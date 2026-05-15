"""E2E Scenario 11 — Concurrent sessions, no cross-talk.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 11.

3 sessions in parallel, each with a deterministic-echo prompt. Assert
each session's messages and audit rows are partitioned cleanly. No event
on session A's SSE stream carries session B's threadId/runId.

AC22.

Milestone gate: M3.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.fixtures.audit_events import RUNNER_SPAWNED


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


async def _run_one_session(
    async_http_client,
    sse_helpers,
    *,
    echo_token: str,
) -> tuple[str, str, list[dict]]:
    """Open a session, send a prompt that elicits a deterministic echo,
    consume to RUN_FINISHED. Returns (session_id, run_id, events)."""
    create = await async_http_client.post("/v1/sessions", json={})
    assert create.status_code == 201
    session_id = create.json()["session_id"]

    run_id = str(uuid.uuid4())
    prompt = (
        f"Respond with exactly the token `{echo_token}` and nothing else. "
        f"Do not add any explanation."
    )
    body = {
        "run_id": run_id,
        "messages": [
            {"id": str(uuid.uuid4()), "role": "user", "content": prompt}
        ],
    }
    events = await sse_helpers.collect_until(
        async_http_client,
        "POST",
        f"/v1/sessions/{session_id}/stream",
        json=body,
        stop_types=("RUN_FINISHED", "RUN_ERROR"),
        timeout=360.0,
    )
    sse_helpers.assert_run_completed(events)
    return session_id, run_id, events


@pytest.mark.asyncio
async def test_scenario_11_concurrent_sessions_no_cross_talk(
    anthropic_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
    docker_ps_for_session,
):
    """3 parallel sessions → 3 isolated containers, 3 isolated event streams, 3 isolated audit partitions (AC22)."""
    from sqlalchemy import select

    from src.db.models import AuditLog, Message

    tokens = ["SESSION_A_OK", "SESSION_B_OK", "SESSION_C_OK"]

    results = await asyncio.gather(
        *(
            _run_one_session(async_http_client, sse_helpers, echo_token=tok)
            for tok in tokens
        )
    )

    session_ids = [r[0] for r in results]
    run_ids = [r[1] for r in results]
    event_lists = [r[2] for r in results]

    assert len(set(session_ids)) == 3
    assert len(set(run_ids)) == 3

    # Each session's messages contain its own echo token only.
    db_session.expire_all()
    for sid_str, token, others in zip(
        session_ids,
        tokens,
        [t for t in (tokens[1:] + tokens[:1] + tokens[2:] + tokens[:2])],
    ):
        sid = uuid.UUID(sid_str)
        msgs = list(
            (
                await db_session.execute(
                    select(Message).where(Message.session_id == sid)
                )
            ).scalars()
        )
        assistant_content = "\n".join(
            m.content for m in msgs if m.role == "assistant"
        )
        assert token in assistant_content, (
            f"session {sid_str} did not echo its token {token!r}; got:\n"
            f"{assistant_content!r}"
        )
        # No other session's token should leak into this session's assistant text.
        for other in tokens:
            if other == token:
                continue
            assert other not in assistant_content, (
                f"session {sid_str} contains other session's token "
                f"{other!r}; cross-talk regression"
            )

    # Per-session audit partition: each session has its own runner_spawned.
    for sid_str in session_ids:
        sid = uuid.UUID(sid_str)
        spawns = list(
            (
                await db_session.execute(
                    select(AuditLog).where(
                        AuditLog.session_id == sid,
                        AuditLog.event_type == RUNNER_SPAWNED,
                    )
                )
            ).scalars()
        )
        assert len(spawns) >= 1, (
            f"session {sid_str} has no runner_spawned audit row"
        )

    # Each SSE event stream carries only its own threadId / runId. The
    # AG-UI 0.1.18 wire emits camelCase keys; events may carry threadId
    # or runId, or neither.
    for sid, rid, events in zip(session_ids, run_ids, event_lists):
        for ev in events:
            tid = ev.get("threadId")
            evt_rid = ev.get("runId")
            if tid is not None:
                assert tid == sid, (
                    f"event for session {sid} carries threadId={tid!r}; "
                    f"cross-session contamination"
                )
            if evt_rid is not None:
                assert evt_rid == rid, (
                    f"event for run {rid} carries runId={evt_rid!r}; "
                    f"cross-run contamination"
                )

    # Distinct Docker containers (one per session_id). Tolerate the case
    # where they've already been warm-idle-evicted — assert was-running
    # at SOME point: include exited containers in the filter.
    seen_container_ids: set[str] = set()
    for sid_str in session_ids:
        ids = docker_ps_for_session(sid_str)
        if ids:
            seen_container_ids.update(ids)
    # At minimum, each label-set must be unique across sessions if any
    # containers are still present (real DockerRunner active).
    if seen_container_ids:
        assert len(seen_container_ids) >= len(session_ids), (
            f"expected at least {len(session_ids)} distinct runner containers; "
            f"got {len(seen_container_ids)} for {session_ids}"
        )
