"""`resolve_tool_call` — Plan task D15. Lifted verbatim from Repo 2
(chatbot-app/agentcore/src/agent/hooks/utils.py).

Unwraps `skill_executor` wrapper calls so hook callbacks see the real
underlying tool name. The 7-line shape that the rest of the codebase
depends on. Keep verbatim — do not "improve" it."""

from __future__ import annotations

from typing import Any


def resolve_tool_call(event: Any) -> tuple[str, dict]:
    """Returns `(tool_name, tool_input)` with skill_executor wrappers
    unwrapped. If the call is not a skill_executor wrap, returns the
    original name+input unchanged."""
    tool_use = event.tool_use
    name = tool_use["name"]
    tool_input = tool_use.get("input", {}) or {}
    if name == "skill_executor" and isinstance(tool_input, dict):
        inner_name = tool_input.get("tool_name")
        inner_input = tool_input.get("tool_input", {}) or {}
        if inner_name:
            return str(inner_name), dict(inner_input)
    return str(name), dict(tool_input)
