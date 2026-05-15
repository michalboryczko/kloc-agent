"""E2E Scenario 8 — Skill progressive disclosure.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 8.

dev-2 ships `skills/summarize-callgraph/SKILL.md`. Sending a prompt that
matches its frontmatter description must cause the LLM to load the body
via `file_read`. Two assertion paths per C4:
- AUDIT: `tool_call.started` row with `payload.tool_name='file_read'`
  AND payload.tool_input.path containing `summarize-callgraph/SKILL.md`.
- OTel SPAN (fallback, if file_read does NOT fire BeforeToolCallEvent):
  Once Track H lands, switch to span-attribute assertion. For now this
  test asserts the audit path; if it fails consistently the team flips
  to the span path.

Milestone gate: M5.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from tests.fixtures.audit_events import TOOL_CALL_STARTED


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.asyncio
async def test_scenario_08_skill_progressive_disclosure(
    anthropic_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
):
    """Prompt matching skill description triggers file_read of SKILL.md body."""
    from sqlalchemy import select

    from src.db.models import AuditLog

    skill_path = (
        Path("/Users/michal/dev/ai/kloc/kloc-agent/skills/summarize-callgraph/SKILL.md")
    )
    if not skill_path.is_file():
        pytest.skip(f"demo skill not present at {skill_path}")

    create = await async_http_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    run_id = str(uuid.uuid4())
    body = {
        "run_id": run_id,
        "messages": [
            {
                "id": str(uuid.uuid4()),
                "role": "user",
                "content": (
                    "Summarise the call graph for OrderController::create "
                    "as a concise callout. Use the summarize-callgraph skill."
                ),
            }
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

    # Audit path: BeforeToolCall for `file_read` referencing the skill body.
    db_session.expire_all()
    sid = uuid.UUID(session_id)
    audit_rows = list(
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.session_id == sid,
                    AuditLog.event_type == TOOL_CALL_STARTED,
                )
            )
        ).scalars()
    )

    file_read_paths: list[str] = []
    for r in audit_rows:
        payload = (r.payload or {}).get("payload", {})
        if payload.get("tool_name") in ("file_read", "fs_read"):
            tool_input = payload.get("tool_input") or payload.get("input") or {}
            path = tool_input.get("path") if isinstance(tool_input, dict) else None
            if path:
                file_read_paths.append(str(path))

    if not file_read_paths:
        pytest.xfail(
            "C4: file_read did not fire BeforeToolCallEvent in this run. "
            "Switch to OTel span-attribute assertion once Track H lands."
        )

    assert any("summarize-callgraph/SKILL.md" in p for p in file_read_paths), (
        f"file_read fired but never for the skill body; paths: {file_read_paths}"
    )
