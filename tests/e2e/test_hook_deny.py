"""E2E Scenario 6 — Hook deny path.

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 6 + AC19.

Two test layers:

- WIRE LEVEL (`@pytest.mark.integration`): POST `BeforeToolCall` webhook
  with the target tool in `KLOC_DENY_TOOLS` → assert response is
  `{"decision": "deny", "reason": "test-deny:<tool>"}` and audit_log
  records the locked `tool_call.denied` event (NOT `tool_call.started`).
  Same body with the deny env unset → `decision: allow` +
  `tool_call.started` audit row. AC19 wire contract.

- E2E LEVEL (`@pytest.mark.e2e`): full compose + real LLM + real MCP +
  real runner setting `event.cancel_tool` → LLM continues without the
  denied tool's output. Stays @skip until dev-3 browser smoke + Phase 6.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest

from src import settings as settings_mod
from src.hooks_audit.policy import Policy
from src.hooks_audit.verify_hmac import sign_for_test


# ---------------------------------------------------------------------------
# Helpers shared with scenario 12 — kept local so the two scenario files
# remain readable in isolation.
# ---------------------------------------------------------------------------


def _make_webhook_headers(
    body_bytes: bytes,
    secret: str | None = None,
    ts_ms: int | None = None,
) -> dict[str, str]:
    ts_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
    secret = secret if secret is not None else settings_mod.get_settings().kloc_hook_secret
    sig = sign_for_test(body_bytes, ts_ms, secret)
    return {
        "Authorization": f"HMAC {sig}",
        "X-Kloc-Hook-Ts": str(ts_ms),
        "Content-Type": "application/json",
    }


def _before_tool_call_body(
    *,
    session_id: uuid.UUID,
    runner_id: str,
    run_id: str,
    tool_name: str,
    tool_call_id: str | None = None,
) -> dict:
    return {
        "event": "BeforeToolCall",
        "runner_id": runner_id,
        "session_id": str(session_id),
        "run_id": run_id,
        "timestamp": int(time.time() * 1000),
        "payload": {
            "tool_call_id": tool_call_id or str(uuid.uuid4()),
            "tool_name": tool_name,
            "input": {"query": "show source of OrderController::create"},
        },
    }


# ---------------------------------------------------------------------------
# Settings-singleton reset helper. `get_settings()` is decorated with
# `@lru_cache(maxsize=1)`; flipping `KLOC_DENY_TOOLS` per-test requires
# clearing that cache (not assigning a non-existent `_settings` attribute).
# ---------------------------------------------------------------------------


@pytest.fixture
def deny_tools(monkeypatch):
    """Yield a setter; clears the get_settings lru_cache before AND after."""

    def _set(value: str) -> None:
        monkeypatch.setenv("KLOC_DENY_TOOLS", value)
        settings_mod.get_settings.cache_clear()

    monkeypatch.setenv("KLOC_DENY_TOOLS", "")
    settings_mod.get_settings.cache_clear()
    try:
        yield _set
    finally:
        settings_mod.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Unit-level Policy contract (no DB, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_policy_allows_when_no_deny_tools(deny_tools):
    """Default settings → allow everything (PoC default per investigation.md §6)."""
    deny_tools("")
    p = Policy()
    decision = p.decide({"event": "BeforeToolCall", "payload": {"tool_name": "kloc_source"}})
    assert decision == {"decision": "allow"}


@pytest.mark.unit
def test_policy_denies_when_tool_in_deny_list(deny_tools):
    """KLOC_DENY_TOOLS=kloc_source,kloc_search → deny kloc_source."""
    deny_tools("kloc_source,kloc_search")
    p = Policy()
    decision = p.decide(
        {"event": "BeforeToolCall", "payload": {"tool_name": "kloc_source"}}
    )
    assert decision["decision"] == "deny"
    assert decision["reason"] == "test-deny:kloc_source"


@pytest.mark.unit
def test_policy_only_intercepts_before_tool_call(deny_tools):
    """AfterToolCall, heartbeat, ArtifactRegistered → always allow (audit-only)."""
    deny_tools("kloc_source")
    p = Policy()
    for event in ("AfterToolCall", "RunnerHeartbeat", "ArtifactRegistered"):
        decision = p.decide(
            {"event": event, "payload": {"tool_name": "kloc_source"}}
        )
        assert decision == {"decision": "allow"}, (
            f"only BeforeToolCall should be gated; got deny for {event}"
        )


@pytest.mark.unit
def test_policy_denies_only_listed_tool(deny_tools):
    """KLOC_DENY_TOOLS=kloc_source: kloc_other still allowed."""
    deny_tools("kloc_source")
    p = Policy()
    assert p.decide(
        {"event": "BeforeToolCall", "payload": {"tool_name": "kloc_other"}}
    ) == {"decision": "allow"}


# ---------------------------------------------------------------------------
# Wire-level webhook contract (in-process backend, real DB)
# ---------------------------------------------------------------------------


pytestmark_int = pytest.mark.integration  # alias to keep marker explicit per test


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webhook_deny_path_returns_deny_decision_and_audit(
    asgi_client, db_session, truncate_all_tables, deny_tools, registered_runner
):
    """AC19: BeforeToolCall for a tool in KLOC_DENY_TOOLS →
    response={"decision":"deny","reason":"test-deny:<tool>"} AND audit row
    written with event_type=`tool_call.denied`."""
    from sqlalchemy import select

    from src.db.models import AuditLog
    from tests.fixtures.audit_events import (
        TOOL_CALL_DENIED,
        TOOL_CALL_STARTED,
    )

    deny_tools("kloc_source")

    create = await asgi_client.post("/v1/sessions", json={})
    session_id = uuid.UUID(create.json()["session_id"])

    runner_id = registered_runner.runner_id
    body_dict = _before_tool_call_body(
        session_id=session_id,
        runner_id=runner_id,
        run_id=str(uuid.uuid4()),
        tool_name="kloc_source",
    )
    body_bytes = json.dumps(body_dict).encode()
    headers = _make_webhook_headers(body_bytes, secret=registered_runner.secret)
    headers["X-Kloc-Hook-Event"] = "BeforeToolCall"

    resp = await asgi_client.post(
        f"/v1/webhooks/runners/{runner_id}/events",
        content=body_bytes,
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["decision"] == "deny"
    assert body["reason"] == "test-deny:kloc_source"

    # Audit row uses the locked vocabulary name `tool_call.denied`,
    # NOT `tool_call.started`.
    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.session_id == session_id)
        )
    ).scalars().all()
    event_types = [r.event_type for r in rows]
    assert TOOL_CALL_DENIED in event_types
    assert TOOL_CALL_STARTED not in event_types, (
        f"denied call must NOT also emit tool_call.started; got {event_types}"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webhook_allow_path_returns_allow_decision_and_audit(
    asgi_client, db_session, truncate_all_tables, deny_tools, registered_runner
):
    """Same body as deny test but with empty KLOC_DENY_TOOLS →
    decision=allow + audit event=tool_call.started."""
    from sqlalchemy import select

    from src.db.models import AuditLog
    from tests.fixtures.audit_events import (
        TOOL_CALL_DENIED,
        TOOL_CALL_STARTED,
    )

    deny_tools("")  # explicit allow-all

    create = await asgi_client.post("/v1/sessions", json={})
    session_id = uuid.UUID(create.json()["session_id"])

    runner_id = registered_runner.runner_id
    body_dict = _before_tool_call_body(
        session_id=session_id,
        runner_id=runner_id,
        run_id=str(uuid.uuid4()),
        tool_name="kloc_source",
    )
    body_bytes = json.dumps(body_dict).encode()
    headers = _make_webhook_headers(body_bytes, secret=registered_runner.secret)
    headers["X-Kloc-Hook-Event"] = "BeforeToolCall"

    resp = await asgi_client.post(
        f"/v1/webhooks/runners/{runner_id}/events",
        content=body_bytes,
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["decision"] == "allow"
    assert "reason" not in body or body.get("reason") in (None, "")

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.session_id == session_id)
        )
    ).scalars().all()
    event_types = [r.event_type for r in rows]
    assert TOOL_CALL_STARTED in event_types
    assert TOOL_CALL_DENIED not in event_types


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webhook_deny_only_targets_listed_tool(
    asgi_client, db_session, truncate_all_tables, deny_tools, registered_runner
):
    """KLOC_DENY_TOOLS=kloc_source: a BeforeToolCall for kloc_search still
    returns allow + tool_call.started audit. Proves the deny list is a
    precise filter, not a global kill switch."""
    from sqlalchemy import select

    from src.db.models import AuditLog
    from tests.fixtures.audit_events import (
        TOOL_CALL_DENIED,
        TOOL_CALL_STARTED,
    )

    deny_tools("kloc_source")

    create = await asgi_client.post("/v1/sessions", json={})
    session_id = uuid.UUID(create.json()["session_id"])

    runner_id = registered_runner.runner_id
    body_dict = _before_tool_call_body(
        session_id=session_id,
        runner_id=runner_id,
        run_id=str(uuid.uuid4()),
        tool_name="kloc_search",  # NOT in deny list
    )
    body_bytes = json.dumps(body_dict).encode()
    headers = _make_webhook_headers(body_bytes, secret=registered_runner.secret)
    headers["X-Kloc-Hook-Event"] = "BeforeToolCall"

    resp = await asgi_client.post(
        f"/v1/webhooks/runners/{runner_id}/events",
        content=body_bytes,
        headers=headers,
    )
    assert resp.status_code == 202
    assert resp.json()["decision"] == "allow"

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.session_id == session_id)
        )
    ).scalars().all()
    event_types = [r.event_type for r in rows]
    assert TOOL_CALL_STARTED in event_types
    assert TOOL_CALL_DENIED not in event_types


# ---------------------------------------------------------------------------
# Full e2e (real LLM + real MCP + real runner) — deferred to Phase 6.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.asyncio
async def test_scenario_06_hook_deny_path(
    anthropic_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
    monkeypatch,
):
    """E2E: real LLM tries denied MCP tool → cancel_tool flips → LLM adapts (AC19).

    Sets `KLOC_DENY_TOOLS=kloc_source` on the backend via env; sends a
    prompt that should trigger kloc_source; asserts:
    - response stream completes (RUN_FINISHED, not RUN_ERROR)
    - audit log records `tool_call.denied` for kloc_source
    - audit log does NOT record `tool_call.started` for kloc_source
    - assistant text is non-empty (LLM adapted)

    Note: this assumes the backend process picks up the env var at the
    Settings boundary. If the running backend was started without
    KLOC_DENY_TOOLS, the operator must restart it with the var set; we
    skip cleanly if the policy is observably allow-all."""
    from sqlalchemy import select

    from src.db.models import AuditLog
    from tests.fixtures.audit_events import (
        TOOL_CALL_DENIED,
        TOOL_CALL_STARTED,
    )

    monkeypatch.setenv("KLOC_DENY_TOOLS", "kloc_source")

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
                    "Get the source of `App\\Ui\\Rest\\Controller\\OrderController::create` "
                    "using the kloc_source tool, then explain it in three bullets."
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
        timeout=180.0,
    )
    sse_helpers.assert_run_completed(events)

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

    # If the runner never called kloc_source (LLM picked a different
    # path), skip — the deny contract is unfalsifiable in this run.
    started_tools = [
        (r.payload or {}).get("payload", {}).get("tool_name")
        for r in audit_rows
        if r.event_type == TOOL_CALL_STARTED
    ]
    denied_tools = [
        (r.payload or {}).get("payload", {}).get("tool_name")
        for r in audit_rows
        if r.event_type == TOOL_CALL_DENIED
    ]
    if "kloc_source" not in started_tools and "kloc_source" not in denied_tools:
        pytest.skip(
            "LLM did not attempt kloc_source on this run; deny path "
            "unfalsifiable. Re-run or adjust the prompt."
        )

    assert "kloc_source" in denied_tools, (
        f"AC19: kloc_source must be denied; denied={denied_tools}, "
        f"started={started_tools}"
    )
    assert "kloc_source" not in started_tools, (
        f"AC19: denied call must NOT also emit tool_call.started; "
        f"started={started_tools}"
    )

    # LLM adapted: non-empty assistant text persisted.
    from src.db.models import Message

    asst = list(
        (
            await db_session.execute(
                select(Message).where(
                    Message.session_id == sid,
                    Message.role == "assistant",
                )
            )
        ).scalars()
    )
    assert any(len(m.content) > 0 for m in asst), (
        "AC19: LLM must produce a response after the tool was denied"
    )
