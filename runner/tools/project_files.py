"""`read_project_file` Strands tool.

Surfaces the runner-side `/projects/<project_name>/<path>` mount as a
single-shot file reader the orchestrator can call when the analyst names
a project and references a concrete file path. Path resolution is
strict (`Path.resolve(strict=True)`) and containment is checked via a
string-prefix on resolved absolute paths so a symlink pointing at a
sibling project or out of the mount cannot escape the scope.

Returns a plain UTF-8 string on success and an `"error: ..."` string on
every refusal path (invalid name, traversal, missing project, non-file,
binary, oversize). Error strings are the visible tool result the agent
observes and re-plans against, so they're stable surface text.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)


# Mirrors `runner/agents_loader.py:_NAME_RE`. Locked at one source of
# truth would mean importing across packages; the regex is tiny and the
# semantic is shared by spec, so duplication is preferred to a coupling
# import.
_PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


# 8 KiB sniff window for the binary heuristic. Large enough to catch
# embedded NULs / non-UTF-8 in the typical header of binary formats,
# small enough to avoid double-reading a file we are about to slice.
_BINARY_SNIFF_BYTES = 8192


_TOOL_DESCRIPTION = (
    "Read a single file from a mounted project source tree at "
    "`/projects/{project_name}/{path}`. Use this whenever the user "
    "names a project and refers to a concrete file path (e.g. "
    "`src/Order/OrderFactory.php` in `kyc`); prefer this tool over "
    "`file_read` for project source. Optionally supply 1-indexed "
    "`start_line`/`end_line` to read a slice of a large file."
)


def _projects_dir() -> str:
    return os.environ.get("KLOC_PROJECTS_DIR", "/projects")


def _max_bytes() -> int | None:
    """Resolve the size cap from settings; treat any import failure as
    no cap so the tool stays usable in test contexts where Settings has
    not been constructed."""
    try:
        from src.settings import get_settings  # local import: avoid src.* at module load
    except Exception:
        return None
    try:
        cfg = get_settings().tool_limits.read_project_file
    except Exception:
        return None
    if cfg is None:
        return None
    return cfg.max_bytes


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def _resolve_under_project(
    project_name: str, path: str
) -> tuple[Path | None, str | None]:
    """Resolve `(project_root, target)` strictly and enforce that the
    target stays under the project root by string-prefix comparison on
    the resolved absolute paths. Returns `(target, None)` on success or
    `(None, error_message)` on rejection."""
    projects_dir = Path(_projects_dir())
    project_root_raw = projects_dir / project_name

    try:
        # `strict=True` makes a symlink to a non-existent target raise
        # FileNotFoundError, which we treat as "not a regular file".
        project_root = project_root_raw.resolve(strict=True)
    except FileNotFoundError:
        return None, f"error: project {project_name} not mounted"
    except OSError:
        return None, f"error: project {project_name} not mounted"
    if not project_root.is_dir():
        return None, f"error: project {project_name} not mounted"

    target_raw = project_root_raw / path
    try:
        target = target_raw.resolve(strict=True)
    except FileNotFoundError:
        return None, f"error: not a regular file: {path}"
    except OSError:
        return None, f"error: not a regular file: {path}"

    # Containment check on resolved paths. Symlinks that resolve to a
    # sibling project under `/projects/<other>/` MUST be rejected — a
    # parent-walk would let `/projects/kyc/danger -> /etc/passwd`
    # slip through if `/etc/passwd` shares any common ancestor.
    if not str(target).startswith(str(project_root) + os.sep):
        return None, f"error: path escapes /projects/{project_name}/"

    return target, None


def _impl(
    project_name: str,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    if not isinstance(project_name, str) or not _PROJECT_NAME_RE.match(
        project_name
    ):
        return "error: invalid project_name; must match ^[a-z][a-z0-9-]*$"
    if not isinstance(path, str) or not path:
        return "error: invalid path; must be a non-empty string"

    target, err = _resolve_under_project(project_name, path)
    if err is not None:
        return err
    assert target is not None

    if not target.is_file():
        return f"error: not a regular file: {path}"

    sliced = start_line is not None or end_line is not None
    if not sliced:
        cap = _max_bytes()
        if cap is not None:
            try:
                size = target.stat().st_size
            except OSError:
                size = 0
            if size > cap:
                return (
                    f"error: file is {_human_bytes(size)} "
                    f"(cap {_human_bytes(cap)}); re-call with "
                    "start_line/end_line, or use kloc_flows for a summary"
                )

    try:
        with target.open("rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
    except OSError as exc:
        return f"error: could not open file: {exc}"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "error: file appears to be binary; this tool returns text only"

    if sliced:
        start = max(1, int(start_line)) if start_line is not None else 1
        end = int(end_line) if end_line is not None else None
        try:
            with target.open("r", encoding="utf-8") as fh:
                lines: list[str] = []
                for idx, line in enumerate(fh, start=1):
                    if end is not None and idx > end:
                        break
                    if idx >= start:
                        lines.append(line)
        except UnicodeDecodeError:
            return "error: file appears to be binary; this tool returns text only"
        except OSError as exc:
            return f"error: could not read file: {exc}"
        return "".join(lines)

    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "error: file appears to be binary; this tool returns text only"
    except OSError as exc:
        return f"error: could not read file: {exc}"


try:
    from strands import tool as _strands_tool  # type: ignore
except ImportError:  # pragma: no cover - Strands optional in unit envs
    _strands_tool = None


if _strands_tool is not None:

    @_strands_tool(description=_TOOL_DESCRIPTION)
    def read_project_file(
        project_name: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read a single file from a mounted project source tree."""
        return _impl(project_name, path, start_line, end_line)

else:

    def read_project_file(  # type: ignore[no-redef]
        project_name: str,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        """Read a single file from a mounted project source tree.

        Plain-Python fallback when the Strands `@tool` decorator is
        unavailable (e.g. unit-test environments)."""
        return _impl(project_name, path, start_line, end_line)
