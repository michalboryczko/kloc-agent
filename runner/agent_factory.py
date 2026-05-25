"""Build a Strands `Agent` and wrap it in `ag_ui_strands.StrandsAgent`.

Composition:
  * model = provider-specific model via `model_factory.create_model`.
  * tools = MCP tools (kloc-intelligence) + subagents discovered from
    `agents/<name>/AGENT.md` via `runner/agents_loader.py` +
    `file_read` for skill-body progressive disclosure.
  * skills_prompt = `discover_skills` + `generate_skills_prompt`.
  * No `session_manager` passed — Postgres is the source of truth.
    History reconciliation happens via `RunAgentInput.messages` on
    every `agent.run(input)` call.

Hook plumbing: `ag_ui_strands.StrandsAgent` constructs a fresh inner
`StrandsAgentCore` per `thread_id` and only forwards what it extracts
from the seed Agent (model / system_prompt / tool_registry /
agent_kwargs). It explicitly excludes hooks from `_extract_agent_kwargs`
because Strands stores them on a HookRegistry post-init. The
constructor accepts a separate `hooks=` kwarg — the only path through
which a per-thread inner agent picks up hook providers. We therefore
build a `HookProvider` that registers the audit callbacks and pass it
via `StrandsAgent(..., hooks=[provider])`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .agents_loader import build_subagents, discover_agents
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
            "audit hook register: BeforeToolCall + AfterToolCall via "
            "HookProvider on per-thread StrandsAgentCore"
        )
        registry.add_callback(
            BeforeToolCallEvent, self._audit_sender.before_tool_call
        )
        registry.add_callback(
            AfterToolCallEvent, self._audit_sender.after_tool_call
        )


def _make_subagent_load_audit_emit(
    audit_sender: AuditHookSender,
) -> Any:
    """Return an async callable the subagent loader uses to report a
    failed AGENT.md load. The callable wraps the audit sender's HMAC
    webhook POST so the backend records one `runner_subagent_load_failed`
    audit row per skipped file."""

    async def _emit(payload: dict) -> None:
        wrapped = audit_sender._build_payload(
            event_name="RunnerSubagentLoadFailed", payload=payload
        )
        try:
            await audit_sender._post(wrapped, "RunnerSubagentLoadFailed")
        except Exception:
            log.exception("agents.subagent_load_failed_audit_post_failed")

    return _emit


def _observe_subagents_loaded_gauge(count: int) -> None:
    """Best-effort emit of the `kloc_agent.runner.subagents_loaded`
    gauge. Skipped silently if OTel is not wired in this runtime
    (unit-test / non-instrumented invocations)."""
    try:
        from opentelemetry import metrics  # type: ignore
    except ImportError:
        return
    try:
        meter = metrics.get_meter(__name__)
        gauge = meter.create_observable_gauge(
            name="kloc_agent.runner.subagents_loaded",
            callbacks=[
                lambda _opts, _c=count: [metrics.Observation(_c)]  # type: ignore[attr-defined]
            ],
            description="Number of subagents successfully loaded at runner startup.",
        )
        # Reference the gauge so the SDK keeps it alive until the
        # process exits; the callback is what the collector reads.
        del gauge
    except Exception:
        log.debug("agents.subagents_loaded_gauge_not_wired")


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
    agents_dir = Path(_field("agents_dir", "/agents") or "/agents")

    model = create_model(model_id=model_id, llm_provider=llm_provider)
    skills_prompt = _load_skills_prompt(skills_dir)
    system_prompt = (
        f"{base_prompt}\n\n{skills_prompt}" if skills_prompt else base_prompt
    )

    mcp_tool_names = {
        getattr(t, "tool_name", None) or getattr(t, "name", None)
        for t in mcp_tools
    }
    mcp_tool_names.discard(None)
    subagent_tools: list[Any] = [*mcp_tools]
    if file_read is not None:
        subagent_tools.append(file_read)
    subagents = build_subagents(
        discover_agents(agents_dir),
        model=model,
        tools=subagent_tools,
        reserved_names=mcp_tool_names,
        audit_emit=_make_subagent_load_audit_emit(audit_sender),
    )
    _observe_subagents_loaded_gauge(len(subagents))

    tools: list[Any] = [*mcp_tools, *subagents]
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
    """Return the AG-UI-emitting adapter.

    `ag_ui_strands.StrandsAgent(agent, StrandsAgentConfig(...),
    hooks=[audit_provider]).run(RunAgentInput)` yields AG-UI events
    directly.

    Passing `audit_provider` is required: the per-thread inner agent
    the wrapper constructs only picks up hooks supplied via the
    `hooks=` kwarg; the seed Agent's hook registry is discarded.
    """
    from ag_ui_strands import StrandsAgent, StrandsAgentConfig  # type: ignore

    hooks_arg = [audit_provider] if audit_provider is not None else None
    return StrandsAgent(agent, StrandsAgentConfig(), hooks=hooks_arg)
