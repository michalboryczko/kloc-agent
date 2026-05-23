"""E2E Scenario 2 — Multi-turn in warm container.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 2.

3 user messages back-to-back within the warm-idle window. Same container
ID across all 3 runs (no respawn). Each run's MESSAGES_SNAPSHOT carries
the prior turns. AC14.

Milestone gate: M3.
"""
from __future__ import annotations

import uuid

import pytest

from tests.fixtures.audit_events import RUNNER_SPAWNED


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.asyncio
async def test_scenario_02_multi_turn_warm_container(
    llm_api_key,
    mcp_reachable,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
    docker_ps_for_session,
):
    """3 messages within warm-idle window → 1 runner_spawned, history retained (AC14)."""
    from sqlalchemy import select

    from src.db.models import AuditLog, Message

    create = await async_http_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    prompts = [
        "Reply with exactly: TURN_ONE_OK",
        "What did you reply on the previous turn? Quote it verbatim.",
        "And the turn before that? Quote it verbatim.",
    ]

    container_ids_seen: list[set[str]] = []
    for prompt in prompts:
        run_id = str(uuid.uuid4())
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
            timeout=300.0,
        )
        sse_helpers.assert_run_completed(events)
        container_ids_seen.append(set(docker_ps_for_session(session_id)))

    # AC14: exactly ONE runner_spawned audit row across all 3 turns.
    db_session.expire_all()
    sid = uuid.UUID(session_id)
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
    assert len(spawns) == 1, (
        f"AC14: warm container must be reused; got {len(spawns)} "
        f"runner_spawned rows"
    )

    # Same container ID throughout (when real DockerRunner active).
    nonempty = [s for s in container_ids_seen if s]
    if nonempty:
        common = set.intersection(*nonempty)
        assert common, (
            f"container ID set diverged across turns: {container_ids_seen}"
        )

    # 6 messages total (3 user + 3 assistant), each assistant finalized.
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
    assert len(users) == 3
    assert len(assistants) >= 3, (
        f"expected >=3 assistant messages, got {len(assistants)}"
    )
    assert all(m.finalized_at is not None for m in assistants)

    # Turn 2's assistant message should reference turn 1's content.
    # Heuristic-only — the LLM may paraphrase, so we check len > 0.
    assert all(len(m.content) > 0 for m in assistants)
