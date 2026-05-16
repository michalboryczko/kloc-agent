"""Build a Strands `Agent` and wrap it in `ag_ui_strands.StrandsAgent`.

Plan tasks D13, D17, E2, E3.

  * model = AnthropicModel via model_factory (constraint 4 — explicit).
  * tools = MCP tools (kloc-intelligence) + sub-agent (summarizer) +
    `file_read` (for skill body progressive disclosure, plan E3).
  * skills_prompt = discover_skills + generate_skills_prompt (plan E2).
  * NO `session_manager` passed (Postgres is SoT, plan investigation §2.1
    option 1). History reconciliation happens via `RunAgentInput.messages`
    on every `agent.run(input)` call.

B-DIAG-A root cause + fix: `ag_ui_strands.StrandsAgent` constructs a
fresh inner `StrandsAgentCore` per thread_id and only forwards what it
extracts from the seed Agent (model / system_prompt / tool_registry /
agent_kwargs). It explicitly EXCLUDES `hooks` from
`_extract_agent_kwargs` because Strands stores hooks as a HookRegistry
after init. The constructor accepts a separate `hooks=` kwarg, which is
the only place a per-thread inner agent picks up hook providers.

Therefore: callbacks registered via `agent.hooks.add_callback(...)` on
the seed Agent are silently dropped. To make audit hooks fire on every
tool call inside the AG-UI loop, we must:
  1. Build a `HookProvider` that registers the audit callbacks.
  2. Pass it via `StrandsAgent(..., hooks=[provider])`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .hooks.audit import AuditHookSender
from .model_factory import create_model

log = logging.getLogger(__name__)


class _AuditHookProvider:
    """Strands `HookProvider` (Protocol) that registers the audit
    sender's BeforeToolCall + AfterToolCall callbacks on the per-thread
    inner agent built by `ag_ui_strands.StrandsAgent`.

    Hook registration is what dispatches the actual audit POSTs in
    `runner/hooks/audit.py`; without going through the
    `StrandsAgent(hooks=[...])` channel the callbacks never fire."""

    def __init__(self, audit_sender: AuditHookSender) -> None:
        self._audit_sender = audit_sender

    def register_hooks(self, registry, **kwargs) -> None:  # type: ignore[no-untyped-def]
        from strands.hooks import (  # type: ignore
            AfterToolCallEvent,
            BeforeToolCallEvent,
        )

        log.info(
            "AUDIT HOOK REGISTER: BeforeToolCall + AfterToolCall via "
            "HookProvider on per-thread StrandsAgentCore"
        )  # B-DIAG-A
        registry.add_callback(
            BeforeToolCallEvent, self._audit_sender.before_tool_call
        )
        registry.add_callback(
            AfterToolCallEvent, self._audit_sender.after_tool_call
        )


def _load_skills_prompt(skills_dir: Path) -> str:
    try:
        from agentskills import discover_skills, generate_skills_prompt  # type: ignore
    except ImportError:
        log.warning(
            "skills.agentskills_not_installed; skipping skills prompt"
        )
        return ""
    try:
        skills = discover_skills(skills_dir)
    except Exception:
        log.exception("skills.discover_failed", extra={"dir": str(skills_dir)})
        return ""
    if not skills:
        return ""
    return generate_skills_prompt(skills)


def build_agent(
    payload: Any,
    mcp_tools: list[Any],
    audit_sender: AuditHookSender,
) -> tuple[Any, _AuditHookProvider]:
    """Create the orchestrator `Agent` for one runner invocation.

    `payload` is the HydrationPayload (Pydantic) or its dict form.
    `mcp_tools` is the result of `MCPClient.list_tools_sync()` collected
    by the entrypoint while inside the `with` block.

    Returns `(seed_agent, audit_provider)`. The seed agent is the
    Strands `Agent` used to seed the AG-UI wrapper; the audit_provider
    is a `HookProvider` that MUST be passed to `wrap_for_agui` so it
    reaches the per-thread inner agent the wrapper constructs.
    """
    from strands import Agent  # type: ignore
    from strands.hooks import (  # type: ignore
        AfterToolCallEvent,
        BeforeToolCallEvent,
    )

    try:
        from strands_tools import file_read  # type: ignore
    except ImportError:
        file_read = None
        log.warning(
            "strands_tools.file_read not available; skill progressive "
            "disclosure may be limited"
        )

    def _field(name: str, default: Any = None) -> Any:
        # Pydantic models have no `.get()`; only attribute access works.
        # Plain dicts use `.get()`. Read both safely without conflating.
        v = getattr(payload, name, None)
        if v is None and isinstance(payload, dict):
            v = payload.get(name, default)
        return v if v is not None else default

    model_id = _field("model_id", "") or ""
    llm_provider = _field("llm_provider")
    base_prompt = _field("system_prompt", "") or ""
    skills_dir = Path(_field("skills_dir", "/skills") or "/skills")

    model = create_model(model_id=model_id, llm_provider=llm_provider)
    skills_prompt = _load_skills_prompt(skills_dir)
    system_prompt = (
        f"{base_prompt}\n\n{skills_prompt}" if skills_prompt else base_prompt
    )

    summarizer = Agent(
        model=model,
        name="summarizer",
        system_prompt=(
            "You receive raw code-intelligence results and produce a "
            "3-bullet executive summary. Cite symbol FQNs verbatim."
        ),
    )

    tools: list[Any] = [*mcp_tools, summarizer]
    if file_read is not None:
        tools.append(file_read)

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
    )
    # Best-effort: also attach to the seed agent's registry so anyone
    # who later invokes the seed directly (tests, non-wrapper paths)
    # still gets the audit hooks. The wrapper-built per-thread inner
    # agent gets them via the HookProvider returned alongside.
    agent.hooks.add_callback(BeforeToolCallEvent, audit_sender.before_tool_call)
    agent.hooks.add_callback(AfterToolCallEvent, audit_sender.after_tool_call)
    return agent, _AuditHookProvider(audit_sender)


def wrap_for_agui(agent: Any, audit_provider: _AuditHookProvider | None = None) -> Any:
    """Return the AG-UI-emitting adapter. Plan D17.

    `ag_ui_strands.StrandsAgent(agent, StrandsAgentConfig(...),
    hooks=[audit_provider]).run(RunAgentInput)` yields AG-UI events
    directly.

    B-DIAG-A fix: pass `audit_provider` so the per-thread inner agent
    constructed by the wrapper picks up the BeforeToolCall +
    AfterToolCall hooks. Without this, audit POSTs never fire because
    the wrapper drops the seed Agent's hook registry on the floor.

    TODO: QA scenario 9 measures `MESSAGES_SNAPSHOT` bandwidth. If the
    snapshot stream proves too chatty, expose `emit_messages_snapshot`
    on StrandsAgentConfig — currently we accept the default."""
    from ag_ui_strands import StrandsAgent, StrandsAgentConfig  # type: ignore

    hooks_arg = [audit_provider] if audit_provider is not None else None
    return StrandsAgent(agent, StrandsAgentConfig(), hooks=hooks_arg)
