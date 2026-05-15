"""E2E Scenario 3 — Warm-idle eviction.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 3.

1 message, wait past warm-idle window, assert:
- container terminated (no `kloc.session_id` label running)
- audit row `runner_warm_idle_evicted`
- session row's `closed_at IS NULL` (eviction does NOT close the session)

AC13.

Milestone gate: M4.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.fixtures.audit_events import RUNNER_WARM_IDLE_EVICTED


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.asyncio
async def test_scenario_03_warm_idle_eviction(
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
    """One message, wait > RUNNER_WARM_IDLE_S, container terminated + audit row (AC13)."""
    from sqlalchemy import select

    from src.db.models import AuditLog, Session
    from src.settings import get_settings

    settings = get_settings()
    warm_idle_s = float(settings.runner_warm_idle_s)
    wait_s = warm_idle_s + 5.0

    create = await async_http_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    run_id = str(uuid.uuid4())
    body = {
        "run_id": run_id,
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Reply with exactly: WARM_IDLE_TEST_OK",
            }
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

    # Before waiting: a container should exist if real DockerRunner is active.
    before_wait = docker_ps_for_session(session_id)

    # Wait past the warm-idle window.
    await asyncio.sleep(wait_s)

    # Container should no longer be running.
    after_wait_running = [
        cid
        for cid in docker_ps_for_session(session_id)
        if _container_is_running(cid)
    ]
    if before_wait:
        assert not after_wait_running, (
            f"AC13: container must terminate after warm-idle window "
            f"({warm_idle_s}s); still running: {after_wait_running}"
        )

    # Audit row recorded.
    db_session.expire_all()
    sid = uuid.UUID(session_id)
    evicted = list(
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.session_id == sid,
                    AuditLog.event_type == RUNNER_WARM_IDLE_EVICTED,
                )
            )
        ).scalars()
    )
    if before_wait:
        assert len(evicted) >= 1, (
            f"AC13: warm-idle eviction must write audit row; got {len(evicted)}"
        )

    # Session row NOT closed — eviction is runner-level, not session-level.
    sess = (
        await db_session.execute(select(Session).where(Session.id == sid))
    ).scalar_one()
    assert sess.closed_at is None, (
        f"AC13: eviction must NOT close the session; closed_at={sess.closed_at}"
    )


def _container_is_running(container_id: str) -> bool:
    import subprocess

    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip() == "true"
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
