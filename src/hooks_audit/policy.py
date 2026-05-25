"""Hook policy decision.

For BeforeToolCall events, the deny-set fallback runs first (stops
the bus when the operator has flagged a tool by name), then a per-tool
`ToolPolicyEvaluator` may inspect `args` and refuse on the basis of
size or unbounded reach. Evaluator denials carry an actionable hint
the agent observes as the tool result.
"""
from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict

from src.settings import Settings, get_settings


log = logging.getLogger(__name__)


Decision = Literal["allow", "deny"]


class PolicyDecision(TypedDict, total=False):
    decision: Decision
    reason: str
    hint: str


try:
    from opentelemetry import metrics as _otel_metrics

    _meter = _otel_metrics.get_meter("kloc_agent.policy")
    _deny_counter = _meter.create_counter(
        "kloc_agent.policy.deny_total",
        description="Tool-call denials emitted by the BeforeToolCall policy layer.",
    )
except Exception:  # pragma: no cover - OTel absence must not crash boot
    _deny_counter = None


def _record_deny(tool: str, reason: str) -> None:
    if _deny_counter is None:
        return
    try:
        _deny_counter.add(1, {"tool": tool, "reason": reason})
    except Exception:
        log.exception("policy.deny_counter_failed")


class Policy:
    """Stateless. The constructor takes a Settings so tests can inject."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def decide(self, event_body: dict[str, Any]) -> PolicyDecision:
        """Inspect a webhook body and return an allow/deny decision."""
        event_type = event_body.get("event") or ""
        if event_type != "BeforeToolCall":
            return {"decision": "allow"}

        payload = event_body.get("payload") or {}
        tool_name = payload.get("tool_name") or ""
        deny_set = self._settings.deny_tools_set

        if tool_name and tool_name in deny_set:
            _record_deny(tool_name, f"test-deny:{tool_name}")
            return {
                "decision": "deny",
                "reason": f"test-deny:{tool_name}",
            }

        from src.hooks_audit.evaluators import EVALUATORS

        evaluator = EVALUATORS.get(tool_name)
        if evaluator is not None:
            args = payload.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            try:
                decision = await evaluator.evaluate(args, settings=self._settings)
            except Exception:
                log.exception("policy.evaluator_failed tool=%s", tool_name)
                decision = None
            if decision is not None:
                _record_deny(tool_name, decision.get("reason", "tool_limit:unknown"))
                return decision

        return {"decision": "allow"}
