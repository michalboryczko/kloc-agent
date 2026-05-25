"""Per-tool argument-aware policy evaluators.

`Policy.decide` looks up an evaluator by `tool_name` and lets it
inspect the call's `args`. `None` means no opinion (allow). A
`PolicyDecision` is returned verbatim, with the `reason` namespaced
under `tool_limit:*` so downstream analysis can distinguish evaluator
denials from the deny-set fallback (`test-deny:*`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .file_read import FileReadEvaluator
from .kloc_flows import KlocFlowsEvaluator
from .read_project_file import ReadProjectFileEvaluator

if TYPE_CHECKING:
    from src.hooks_audit.policy import PolicyDecision
    from src.settings import Settings


@runtime_checkable
class ToolPolicyEvaluator(Protocol):
    async def evaluate(
        self, args: dict, *, settings: "Settings | None" = ...
    ) -> "PolicyDecision | None": ...


EVALUATORS: dict[str, ToolPolicyEvaluator] = {
    "file_read": FileReadEvaluator(),
    "kloc_flows": KlocFlowsEvaluator(),
    "read_project_file": ReadProjectFileEvaluator(),
}


__all__ = [
    "EVALUATORS",
    "FileReadEvaluator",
    "KlocFlowsEvaluator",
    "ReadProjectFileEvaluator",
    "ToolPolicyEvaluator",
]
