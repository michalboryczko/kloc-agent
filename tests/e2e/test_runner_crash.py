"""E2E Scenario 7 — Runner crash mid-tool-call.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 7.

Force `docker kill -9` on the container during a TOOL_CALL_START. Backend
detects heartbeat-dead within RUNNER_HEARTBEAT_TIMEOUT_S; writes audit
rows `runner_heartbeat_lost` + `tool_call.crashed`; emits RUN_ERROR on
SSE. Subsequent retry spawns a fresh runner (no auto-restart).

AC20, AC21.

Milestone gate: M4.
"""
from __future__ import annotations

import asyncio
import subprocess
import uuid

import pytest

from tests.fixtures.audit_events import (
    RUNNER_HEARTBEAT_LOST,
    RUNNER_SPAWNED,
    TOOL_CALL_CRASHED,
)


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _kill_session_container(session_id: str) -> str | None:
    """SIGKILL the runner container labelled with this session_id.

    Returns the killed container ID, or None if none was running.
    """
    try:
        out = subprocess.check_output(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=kloc.session_id={session_id}",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    container_id = out.splitlines()[0]
    subprocess.run(
        ["docker", "kill", "-s", "KILL", container_id],
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=5,
    )
    return container_id


@pytest.mark.asyncio
async def test_scenario_07_runner_crash_mid_tool_call(
    llm_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
):
    """Kill -9 during TOOL_CALL_START → heartbeat dead → RUN_ERROR + tool_call.crashed (AC20, AC21)."""
    from sqlalchemy import select

    from src.db.models import AuditLog
    from src.settings import get_settings

    settings = get_settings()
    heartbeat_timeout_s = float(settings.runner_heartbeat_timeout_s)

    create = await async_http_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    run_id = str(uuid.uuid4())
    body = {
        "run_id": run_id,
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Use kloc tools to find handlers of OrderPlaced.",
            }
        ],
    }

    killed_container: str | None = None
    saw_tool_call_start = False
    final_events: list[dict] = []

    async def _consume_until_kill_then_drain() -> None:
        nonlocal killed_container, saw_tool_call_start
        async with sse_helpers.sse_request(
            async_http_client,
            "POST",
            f"/v1/sessions/{session_id}/stream",
            json=body,
            timeout=heartbeat_timeout_s + 60,
        ) as stream:
            async for ev in stream:
                final_events.append(ev)
                t = ev.get("type")
                if t == "TOOL_CALL_START" and killed_container is None:
                    saw_tool_call_start = True
                    killed_container = _kill_session_container(session_id)
                if t in ("RUN_FINISHED", "RUN_ERROR"):
                    return

    try:
        await asyncio.wait_for(
            _consume_until_kill_then_drain(),
            timeout=heartbeat_timeout_s + 90,
        )
    except asyncio.TimeoutError:
        pytest.fail(
            f"backend did not surface RUN_ERROR within "
            f"{heartbeat_timeout_s + 90}s of kill -9; AC20 regressed"
        )

    if not saw_tool_call_start or killed_container is None:
        pytest.skip(
            "no container observed for this session (DockerRunner not spawning "
            "or registry is the stub); AC20 needs the real DockerRunner"
        )

    # SSE stream terminated with RUN_ERROR (NOT RUN_FINISHED).
    last = final_events[-1] if final_events else {}
    assert last.get("type") == "RUN_ERROR", (
        f"expected RUN_ERROR after kill; got tail={last!r}"
    )

    # No auto-restart: container is gone.
    still_alive = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=kloc.session_id={session_id}",
        ],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
    assert not still_alive, (
        f"runner must NOT auto-restart after crash; still alive: {still_alive!r}"
    )

    # Audit: runner_heartbeat_lost + tool_call.crashed both present (AC20).
    db_session.expire_all()
    sid = uuid.UUID(session_id)
    audit_types = [
        r.event_type
        for r in (
            await db_session.execute(
                select(AuditLog).where(AuditLog.session_id == sid)
            )
        ).scalars()
    ]
    assert RUNNER_HEARTBEAT_LOST in audit_types, (
        f"AC20: backend must record runner_heartbeat_lost; got "
        f"{sorted(set(audit_types))}"
    )
    assert TOOL_CALL_CRASHED in audit_types, (
        f"AC20: in-flight tool call must be marked tool_call.crashed; got "
        f"{sorted(set(audit_types))}"
    )

    # AC21: retry on same session_id spawns a FRESH runner.
    retry_run_id = str(uuid.uuid4())
    retry_body = {
        "run_id": retry_run_id,
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": "Please retry the previous query.",
            }
        ],
    }
    retry_events = await sse_helpers.collect_until(
        async_http_client,
        "POST",
        f"/v1/sessions/{session_id}/stream",
        json=retry_body,
        stop_types=("RUN_FINISHED", "RUN_ERROR"),
        timeout=300.0,
    )
    sse_helpers.assert_run_completed(retry_events)

    db_session.expire_all()
    spawn_count = len(
        list(
            (
                await db_session.execute(
                    select(AuditLog).where(
                        AuditLog.session_id == sid,
                        AuditLog.event_type == RUNNER_SPAWNED,
                    )
                )
            ).scalars()
        )
    )
    assert spawn_count >= 2, (
        f"AC21: retry must spawn a FRESH runner; got {spawn_count} "
        f"runner_spawned rows (expected >= 2)"
    )
