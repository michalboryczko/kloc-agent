"""Hook policy decision (Phase 1.C-1.4).

PoC default: allow-all. For QA scenario 10 / AC19, the `KLOC_DENY_TOOLS`
env var (set on the backend container) names tools that should be denied;
the receiver returns `{"decision": "deny", "reason": "test-deny:<tool>"}`
so the runner sets `event.cancel_tool = reason` and Strands aborts the
tool inline.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict

from src.settings import Settings, get_settings


Decision = Literal["allow", "deny"]


class PolicyDecision(TypedDict, total=False):
    decision: Decision
    reason: str


class Policy:
    """Stateless. The constructor takes a Settings so tests can inject."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def decide(self, event_body: dict[str, Any]) -> PolicyDecision:
        """Inspect a webhook body and return an allow/deny decision."""
        event_type = event_body.get("event") or ""
        if event_type != "BeforeToolCall":
            # AfterToolCall, RunnerHeartbeat, etc. are audit-only — allow.
            return {"decision": "allow"}

        payload = event_body.get("payload") or {}
        tool_name = payload.get("tool_name") or ""
        deny_set = self._settings.deny_tools_set

        if tool_name and tool_name in deny_set:
            return {
                "decision": "deny",
                "reason": f"test-deny:{tool_name}",
            }

        return {"decision": "allow"}
