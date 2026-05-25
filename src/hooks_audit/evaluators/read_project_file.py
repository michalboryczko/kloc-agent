"""read_project_file argument-aware policy evaluator.

Denies whole-file reads when the resolved path under the backend-side
view of the projects volume (`/projects-source/<project_name>/<path>`)
exceeds `KLOC_TOOL_LIMITS.read_project_file.max_bytes`. A byte-range
read (`start_line`/`end_line`) bypasses the stat check — the runner-side
tool slices the file line-by-line, so a 5 MiB file with a 50-line slice
is still permitted iff the slice fits under the runner's read budget.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from src.settings import Settings, get_settings

if TYPE_CHECKING:
    from src.hooks_audit.policy import PolicyDecision


log = logging.getLogger(__name__)


_PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def _backend_projects_dir() -> str:
    return os.environ.get("KLOC_PROJECTS_BACKEND_DIR", "/projects-source")


class ReadProjectFileEvaluator:
    async def evaluate(
        self, args: dict, *, settings: Settings | None = None
    ) -> "PolicyDecision | None":
        cfg = (settings or get_settings()).tool_limits.read_project_file
        if cfg is None or cfg.max_bytes is None:
            return None

        project_name = args.get("project_name")
        path = args.get("path")
        if not isinstance(project_name, str) or not _PROJECT_NAME_RE.match(
            project_name
        ):
            return None
        if not isinstance(path, str) or not path:
            return None

        # A line-slice read bypasses the cap: the runner-side tool
        # reads the file line-by-line and emits only the requested
        # range, so a large source file can still be sliced under the
        # cap on the wire.
        if args.get("start_line") is not None or args.get("end_line") is not None:
            return None

        root = Path(_backend_projects_dir()) / project_name
        try:
            project_root = root.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return None
        target_raw = project_root / path
        try:
            target = target_raw.resolve(strict=True)
        except (FileNotFoundError, OSError):
            return None
        if not str(target).startswith(str(project_root) + os.sep):
            return None
        try:
            if not target.is_file():
                return None
            size = target.stat().st_size
        except OSError:
            return None

        cap = cfg.max_bytes
        if size <= cap:
            return None

        hint = (
            f"file is {_human_bytes(size)} (cap {_human_bytes(cap)}); "
            "re-call with start_line/end_line, or use kloc_flows for a summary"
        )
        return {
            "decision": "deny",
            "reason": "tool_limit:file_too_large",
            "hint": hint,
        }
