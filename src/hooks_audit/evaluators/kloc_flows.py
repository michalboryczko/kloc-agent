"""kloc_flows argument-aware policy evaluator.

When `KLOC_TOOL_LIMITS.kloc_flows.require_bounded` is true and the
call carries neither `depth` nor `limit`, the call is denied with a
hint that suggests a concrete bounded alternative.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.settings import Settings, get_settings

if TYPE_CHECKING:
    from src.hooks_audit.policy import PolicyDecision


class KlocFlowsEvaluator:
    async def evaluate(
        self, args: dict, *, settings: Settings | None = None
    ) -> "PolicyDecision | None":
        cfg = (settings or get_settings()).tool_limits.kloc_flows
        if cfg is None or not cfg.require_bounded:
            return None

        if args.get("depth") is not None or args.get("limit") is not None:
            return None

        return {
            "decision": "deny",
            "reason": "tool_limit:unbounded",
            "hint": (
                "kloc_flows requires depth or limit; "
                "try kloc_flows(depth=2) for an overview"
            ),
        }
