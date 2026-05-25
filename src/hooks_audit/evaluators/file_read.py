"""file_read argument-aware policy evaluator.

Denies whole-file reads of paths whose on-disk size exceeds
`KLOC_TOOL_LIMITS.file_read.max_bytes`. A byte-range read
(`start_line`/`end_line` or explicit `max_bytes`) is trusted as a
bounded read and skipped — the evaluator does not call the stat
endpoint for those.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.hooks_audit import stat_client
from src.settings import Settings, get_settings

if TYPE_CHECKING:
    from src.hooks_audit.policy import PolicyDecision


log = logging.getLogger(__name__)


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


class FileReadEvaluator:
    async def evaluate(
        self, args: dict, *, settings: Settings | None = None
    ) -> "PolicyDecision | None":
        cfg = (settings or get_settings()).tool_limits.file_read
        if cfg is None or cfg.max_bytes is None:
            return None

        path = args.get("path")
        if not path or not isinstance(path, str):
            return None

        if (
            args.get("start_line") is not None
            or args.get("end_line") is not None
            or args.get("max_bytes") is not None
        ):
            return None

        base_url = (settings or get_settings()).intelligence_stat_url
        info = await stat_client.stat(path, base_url=base_url)
        if info is None:
            return None
        if not info.get("exists") or not info.get("is_file"):
            return None

        size = info["size_bytes"]
        cap = cfg.max_bytes
        if size <= cap:
            return None

        hint = (
            f"file is {_human_bytes(size)} (cap {_human_bytes(cap)}); "
            f"re-call with start_line/end_line, or use kloc_flows for a summary"
        )
        return {
            "decision": "deny",
            "reason": "tool_limit:file_too_large",
            "hint": hint,
        }
