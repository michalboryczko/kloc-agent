"""Subagent autoregistry.

Walks `agents_dir/<name>/AGENT.md`, parses YAML frontmatter + markdown
body, and constructs Strands `Agent` instances that the orchestrator
exposes as tools (agents-as-tools pattern). Mirrors the shape of
`agentskills.discovery` so operators reason about one loader pattern
across `/skills` and `/agents`.

Failure modes are quiet skips, not fatal: a single malformed AGENT.md
must not take down the runner for every other session pointing at the
same `/agents` named volume.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)


_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class AgentSpec(BaseModel):
    """Parsed `AGENT.md` contents.

    `name` becomes the Strands `Agent.name` and therefore the tool name
    the orchestrator's LLM sees. `description` is what the LLM reads
    when deciding whether to delegate. `system_prompt` is the markdown
    body — author-owned, no template prefix or suffix is injected.
    """

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    system_prompt: str
    path: Path

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "name must match ^[a-z][a-z0-9-]*$ and be 1-64 chars"
            )
        return v


def _parse_agent_md(content: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise ValueError(
            "AGENT.md must start with YAML frontmatter (---) and close with ---"
        )
    frontmatter_str = match.group(1)
    body = match.group(2).strip()
    parsed = yaml.safe_load(frontmatter_str)
    if not isinstance(parsed, dict):
        raise ValueError("AGENT.md frontmatter must be a YAML mapping")
    return parsed, body


def _load_spec(agent_md: Path) -> AgentSpec:
    content = agent_md.read_text(encoding="utf-8")
    frontmatter, body = _parse_agent_md(content)
    if "name" not in frontmatter:
        raise ValueError("missing required frontmatter field: name")
    if "description" not in frontmatter:
        raise ValueError("missing required frontmatter field: description")
    name = frontmatter["name"]
    description = frontmatter["description"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("frontmatter field 'name' must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(
            "frontmatter field 'description' must be a non-empty string"
        )
    return AgentSpec(
        name=name.strip(),
        description=description.strip(),
        system_prompt=body,
        path=agent_md,
    )


def discover_agents(agents_dir: Path) -> list[AgentSpec]:
    """Walk `agents_dir/*/AGENT.md`, return parsed specs sorted by name.

    Returns `[]` on a missing or non-directory path. Skips invalid
    individual files with a `log.warning` so a single bad pack does
    not poison the rest.
    """
    agents_dir = Path(agents_dir).expanduser()
    if not agents_dir.exists():
        log.info("agents.dir_missing", extra={"dir": str(agents_dir)})
        return []
    if not agents_dir.is_dir():
        log.warning("agents.path_not_directory", extra={"dir": str(agents_dir)})
        return []

    specs: list[AgentSpec] = []
    seen_names: set[str] = set()
    for child in sorted(agents_dir.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        agent_md = child / "AGENT.md"
        if not agent_md.exists():
            log.debug("agents.no_agent_md", extra={"dir": str(child)})
            continue
        try:
            spec = _load_spec(agent_md)
        except Exception as exc:
            log.warning(
                "agents.invalid_spec",
                extra={"path": str(agent_md), "reason": str(exc)},
            )
            continue
        if spec.name in seen_names:
            log.warning(
                "agents.duplicate_name",
                extra={"path": str(agent_md), "agent_name": spec.name},
            )
            continue
        seen_names.add(spec.name)
        specs.append(spec)

    specs.sort(key=lambda s: s.name)
    log.info(
        "agents.discovered",
        extra={"dir": str(agents_dir), "count": len(specs)},
    )
    return specs


def build_subagents(
    specs: list[AgentSpec],
    model: Any,
    tools: list[Any],
    reserved_names: set[str],
    audit_emit: Callable[[dict], Awaitable[None]] | None = None,
) -> list[Any]:
    """Instantiate one Strands `Agent` per spec; skip MCP-name collisions.

    `reserved_names` is the set of MCP tool names advertised at runner
    startup. The MCP tool wins on collision because renaming an MCP
    tool is not recoverable; removing a subagent is.

    When `audit_emit` is provided, a skip emits a payload shaped for
    the `runner_subagent_load_failed` audit event so operators see the
    failure without coupling agent-pack edits to runner uptime.
    """
    from strands import Agent  # type: ignore

    constructed: list[Any] = []
    for spec in specs:
        if spec.name in reserved_names:
            log.warning(
                "agents.name_conflicts_with_mcp_tool",
                extra={"agent_name": spec.name, "path": str(spec.path)},
            )
            if audit_emit is not None:
                payload = {
                    "file": str(spec.path),
                    "reason": "name_conflicts_with_mcp_tool",
                    "detail": (
                        f"subagent name {spec.name!r} collides with an "
                        "MCP tool advertised at runner startup"
                    ),
                }
                _fire_and_forget_audit(audit_emit, payload)
            continue
        constructed.append(
            Agent(
                model=model,
                name=spec.name,
                description=spec.description,
                system_prompt=spec.system_prompt,
                tools=tools,
            )
        )
    return constructed


def _fire_and_forget_audit(
    audit_emit: Callable[[dict], Awaitable[None]], payload: dict
) -> None:
    """Best-effort schedule of the audit coroutine.

    Called from `build_subagents`, which is a sync function invoked
    during runner startup; if a loop is available we schedule on it,
    otherwise we run the coroutine to completion via `asyncio.run`.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            asyncio.run(audit_emit(payload))
        except Exception:
            log.exception("agents.audit_emit_failed")
        return
    loop.create_task(audit_emit(payload))
