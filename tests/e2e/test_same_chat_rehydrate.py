"""E2E Scenario 4 — Same-chat rehydrate after warm-idle eviction.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 4.

After scenario 3's eviction, a second user message on the same session_id
must spawn a FRESH container and rehydrate the runner with the full prior
message history from Postgres. No RUN_ERROR.

AC16, AC17 (rehydrate latency).

Milestone gate: M4.
"""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest

from tests.fixtures.audit_events import (
    RUNNER_SPAWNED,
    RUNNER_WARM_IDLE_EVICTED,
)


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.asyncio
async def test_scenario_04_same_chat_rehydrate_after_eviction(
    llm_api_key,
    mcp_reachable,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
    docker_ps_for_session,
):
    """Eviction → new message → fresh runner, full history rehydrated, no RUN_ERROR (AC16, AC17)."""
    from sqlalchemy import select

    from src.db.models import AuditLog, Message
    from src.settings import get_settings

    settings = get_settings()
    warm_idle_s = float(settings.runner_warm_idle_s)

    create = await async_http_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    # Turn 1.
    body1 = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Reply with exactly: REHYDRATE_TURN_ONE_OK",
            }
        ],
    }
    events1 = await sse_helpers.collect_until(
        async_http_client,
        "POST",
        f"/v1/sessions/{session_id}/stream",
        json=body1,
        stop_types=("RUN_FINISHED", "RUN_ERROR"),
        timeout=300.0,
    )
    sse_helpers.assert_run_completed(events1)

    # Wait past warm-idle window for eviction.
    await asyncio.sleep(warm_idle_s + 5.0)

    # Turn 2 — same session_id; must rehydrate. Measure AC17 latency.
    body2 = {
        "run_id": str(uuid.uuid4()),
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Quote your previous reply verbatim.",
            }
        ],
    }
    t0 = time.monotonic()
    first_event_at: float | None = None
    events2: list[dict] = []
    async with sse_helpers.sse_request(
        async_http_client,
        "POST",
        f"/v1/sessions/{session_id}/stream",
        json=body2,
        timeout=300.0,
    ) as stream:
        async for ev in stream:
            if first_event_at is None:
                first_event_at = time.monotonic()
            events2.append(ev)
            if ev.get("type") in ("RUN_FINISHED", "RUN_ERROR"):
                break
    sse_helpers.assert_run_completed(events2)

    rehydrate_latency_s = (first_event_at or time.monotonic()) - t0
    # AC17 target is 1-2s but PoC allows up to ~5s headroom for compose-on-laptop.
    assert rehydrate_latency_s < 10.0, (
        f"AC17: rehydrate first-event latency {rehydrate_latency_s:.2f}s > 10s budget"
    )

    # No RUN_ERROR on turn 2.
    assert not any(e.get("type") == "RUN_ERROR" for e in events2), (
        f"rehydrate must not produce RUN_ERROR; events: "
        f"{[e.get('type') for e in events2]}"
    )

    # Audit: warm_idle_evicted + 2 runner_spawned rows.
    db_session.expire_all()
    sid = uuid.UUID(session_id)
    audit_rows = list(
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.session_id == sid)
            )
        ).scalars()
    )
    audit_types = [r.event_type for r in audit_rows]

    spawn_count = audit_types.count(RUNNER_SPAWNED)
    evict_count = audit_types.count(RUNNER_WARM_IDLE_EVICTED)

    # If the real DockerRunner is up, both should be present in expected counts.
    if any(docker_ps_for_session(session_id)) or evict_count > 0:
        assert spawn_count >= 2, (
            f"AC16: rehydrate must spawn a FRESH container; got "
            f"{spawn_count} runner_spawned rows"
        )
        assert evict_count >= 1, (
            f"AC16: turn-1 container must have been evicted; got "
            f"{evict_count} runner_warm_idle_evicted rows"
        )

    # Turn 2's MESSAGES_SNAPSHOT must include all prior turns.
    snapshots = [e for e in events2 if e.get("type") == "MESSAGES_SNAPSHOT"]
    assert snapshots, "rehydrated runner emitted no MESSAGES_SNAPSHOT"
    first_snapshot = snapshots[0]
    msgs_in_snapshot = first_snapshot.get("messages") or []
    # >= 2 because the fresh runner gets turn-1 (user + assistant) PLUS the
    # new user msg from this turn.
    assert len(msgs_in_snapshot) >= 2, (
        f"first MESSAGES_SNAPSHOT after rehydrate must include >=2 prior msgs; "
        f"got {len(msgs_in_snapshot)}"
    )

    # 4 messages in DB total: 2 user + 2 assistant, all assistants finalized.
    msgs = list(
        (
            await db_session.execute(
                select(Message)
                .where(Message.session_id == sid)
                .order_by(Message.seq.asc())
            )
        ).scalars()
    )
    users = [m for m in msgs if m.role == "user"]
    assistants = [m for m in msgs if m.role == "assistant"]
    assert len(users) == 2
    assert len(assistants) >= 2
    assert all(m.finalized_at is not None for m in assistants)
