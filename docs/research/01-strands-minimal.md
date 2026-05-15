# 01 — Strands minimal usage

Scope: the smallest runnable PoC that proves all four agent capabilities (MCP tool, sub-agent, skill, hook). Versions verified at time of writing: `strands-agents` **v1.39.0** (released 2026-05-08), `strands_agentskills` **0.2.0** (source-only). Python **3.10+**.

Out of scope: persistence, streaming wire format, runner lifecycle, AG-UI / CopilotKit, OTel collector wiring. Those live in sibling research notes.

---

## 1. Quickstart snippet

One file. Drop it next to a `./skills/` directory containing at least one skill (`skills/<name>/SKILL.md`). Requires `ANTHROPIC_API_KEY` exported and `uv run kloc-intelligence mcp-server --database <name>` runnable from PATH.

```python
# poc.py
"""Strands minimal PoC: MCP tool + sub-agent + skill + hook."""
from pathlib import Path

from mcp import StdioServerParameters, stdio_client

from strands import Agent
from strands.hooks import BeforeToolCallEvent
from strands.models.anthropic import AnthropicModel
from strands.tools.mcp import MCPClient

from agentskills import discover_skills, generate_skills_prompt  # from sample-strands-agents-agentskills

# --- 1. Model (Anthropic; Bedrock is the default but needs AWS creds) -------
model = AnthropicModel(
    client_args={"api_key": None},  # falls back to ANTHROPIC_API_KEY
    model_id="claude-sonnet-4-6",
    max_tokens=4096,
)

# --- 2. MCP tool from kloc-intelligence stdio server ------------------------
# Strands spawns the subprocess for us; lifecycle is tied to the MCPClient
# context manager.
kloc_mcp = MCPClient(lambda: stdio_client(
    StdioServerParameters(
        command="uv",
        args=["run", "kloc-intelligence", "mcp-server", "--database", "demo"],
    )
))

# --- 3. Skill discovery (progressive disclosure, level-1 metadata only) -----
skills = discover_skills(Path("./skills"))
skills_prompt = generate_skills_prompt(skills)

# --- 4. Sub-agent: a focused "summarizer" exposed via agents-as-tools -------
summarizer = Agent(
    model=model,
    name="summarizer",
    system_prompt=(
        "You receive raw code-intelligence results and produce a 3-bullet "
        "executive summary. Cite symbol FQNs verbatim."
    ),
)

# --- 5. Hook: audit every tool call (in-process; the policy layer will wrap
#       this in an HTTP webhook later — see section 4). --------------------
def audit_tool_call(event: BeforeToolCallEvent) -> None:
    tool_name = event.tool_use["name"]
    print(f"[audit] tool={tool_name} input={event.tool_use.get('input')!r}")

# --- 6. Orchestrator agent --------------------------------------------------
with kloc_mcp:  # explicit lifecycle => predictable shutdown on exceptions
    mcp_tools = kloc_mcp.list_tools_sync()

    orchestrator = Agent(
        model=model,
        system_prompt=(
            "You are a code-intelligence analyst. Use kloc MCP tools to "
            "look things up, then call the `summarizer` sub-agent for the "
            "final response.\n\n" + skills_prompt
        ),
        tools=[*mcp_tools, summarizer],  # sub-agent passed as a tool
    )
    orchestrator.hooks.add_callback(BeforeToolCallEvent, audit_tool_call)

    result = orchestrator("Find handlers of OrderPlaced and summarise them.")
    print(result)
```

Run: `uv run python poc.py`. The hook fires before every MCP tool *and* before the `summarizer` sub-agent invocation (sub-agents-as-tools route through the same tool-call lifecycle).

---

## 2. API surface used

| Symbol | Role | Import |
|---|---|---|
| `Agent` | Agent loop / orchestrator | `from strands import Agent` |
| `AnthropicModel` | Model provider; Bedrock is default | `from strands.models.anthropic import AnthropicModel` |
| `MCPClient` | Wraps an MCP transport; lazy-connects on first use | `from strands.tools.mcp import MCPClient` |
| `stdio_client`, `StdioServerParameters` | Transport factory (re-exported from `mcp` SDK) | `from mcp import stdio_client, StdioServerParameters` |
| `BeforeToolCallEvent` | Pre-tool hook event | `from strands.hooks import BeforeToolCallEvent` |
| `agent.hooks.add_callback(EventType, fn)` | Registers a typed callback on the `HookRegistry` exposed on every agent | (instance method) |
| `discover_skills(path)` | Scans a directory, returns `SkillProperties[]` | `from agentskills import discover_skills` |
| `generate_skills_prompt(skills)` | Renders level-1 metadata into a system-prompt fragment | `from agentskills import generate_skills_prompt` |
| `Agent(..., tools=[sub_agent])` | Sub-agent-as-tool (no wrapper class needed) | — |

Other hook events available off `strands.hooks`: `AgentInitializedEvent`, `BeforeInvocationEvent`/`AfterInvocationEvent`, `BeforeModelCallEvent`/`AfterModelCallEvent`, `AfterToolCallEvent`, `MessageAddedEvent`, plus multi-agent variants `BeforeNodeCallEvent`/`AfterNodeCallEvent` and `Before|AfterMultiAgentInvocationEvent`. Also exported: `HookCallback`, `HookRegistry`, `HookProvider` (interface for grouped registration).

---

## 3. Sub-agent pattern choice — agents-as-tools

Strands offers three multi-agent primitives: **agents-as-tools**, **graph**, and **swarm**.

We pick **agents-as-tools** for the PoC because:

- It is literally one line: `tools=[sub_agent]`. The SDK auto-wraps any `Agent` placed in the tool list; no separate class is needed. `Agent.as_tool(name="...")` is available if you need to rename it.
- It reuses the existing tool-call lifecycle — meaning `BeforeToolCallEvent` fires for sub-agent delegation too, so the audit hook satisfies success criterion #4 without extra work.
- Graph/swarm require defining nodes, edges, or orchestrator policies and are overkill for "delegate to one specialist".

Move to **graph** once we have ≥3 fixed-topology sub-agents with deterministic routing; **swarm** if we want emergent peer hand-off. Neither is needed for the PoC.

---

## 4. Hook choice — `BeforeToolCallEvent`

Simplest event to wire and the most useful for our audit/policy layer.

Signature: `def callback(event: BeforeToolCallEvent) -> None`. The event is a dataclass with these (mutable) fields:

- `selected_tool: AgentTool | None` — overwrite to substitute a different tool
- `tool_use: ToolUse` — the call payload; `tool_use["name"]` and `tool_use["input"]`
- `invocation_state: dict[str, Any]` — shared kwargs across the invocation
- `cancel_tool: bool | str = False` — set to a string and the SDK aborts the tool call with that message (this is our policy-deny mechanism)

Registration: `agent.hooks.add_callback(BeforeToolCallEvent, my_fn)`. There is also a `HookProvider` interface (`register_hooks(self, registry)` method) for grouping callbacks into reusable plugins.

**Sync vs webhook.** Strands hooks are **in-process Python callbacks only** — there is no built-in HTTP-webhook dispatcher. To meet the project's policy-layer requirement (POST to backend), wrap a small `httpx.post(...)` inside the callback. For the PoC keep it in-process; for production move it into a `HookProvider` and dispatch async via `httpx.AsyncClient`. Callbacks can be `async def` (the SDK awaits them).

---

## 5. Skill loading mechanism

The `aws-samples/sample-strands-agents-agentskills` plugin is **source-installed** (no PyPI) — `pip install -e git+https://github.com/aws-samples/sample-strands-agents-agentskills`. Package name on disk: `strands_agentskills` 0.2.0, imported as `agentskills`.

There is **no** `Agent(skills_dir=...)` parameter. The loading API is explicit:

1. `discover_skills(path)` walks `./skills/*/SKILL.md`, parses the YAML frontmatter (`name`, `description`, optional `allowed-tools`), and returns metadata-only `SkillProperties` objects. ~100 tokens per skill.
2. `generate_skills_prompt(skills)` renders those into a system-prompt block.
3. **Progressive disclosure** is then driven by the LLM itself: when it judges a skill relevant, it uses `file_read` (from `strands_tools`) to load the SKILL.md body, and only then any referenced resources. The plugin assumes the agent has filesystem read access — so include `from strands_tools import file_read` in `tools=[...]`.

Alternative patterns the plugin ships: `create_skill_tool()` (skill exposed as a tool the LLM calls) and `create_skill_agent_tool()` (skill executed in an isolated sub-agent — interesting later, since it composes naturally with our agents-as-tools choice).

Skill files follow the **Anthropic Agent Skills** spec: a directory per skill, `SKILL.md` with YAML frontmatter (`name`: ≤64 chars, lowercase-hyphen; `description`: ≤1024 chars), plus optional sibling files and `scripts/` referenced by the body.

---

## 6. MCP wiring

**The Strands `MCPClient` spawns the MCP subprocess itself** when given `StdioServerParameters(command=..., args=...)`. The client connects lazily (first `list_tools_sync()` or first tool invocation) and shuts the subprocess down when the context manager exits.

Two usage shapes:

- **Managed** (simplest): `Agent(tools=[mcp_client])`. The SDK manages the lifecycle implicitly. Good for a single MCP server, fire-and-forget.
- **Explicit `with` block** (used in the snippet): gives us `list_tools_sync()` access, deterministic teardown on exception, and a clear nesting point for `agent.hooks.add_callback(...)` registration before the first tool call. Recommended for our setup because the runner is per-session and we want predictable shutdown when the session ends.

For long-lived per-session runners the recommended pattern is **subprocess managed by Strands inside the runner process** — one MCP server per session, dying with the runner. Don't run the MCP server as an external long-lived daemon; stdio is point-to-point and the protocol expects a child-process relationship.

---

## 7. Session management primitive

`strands.session.*` provides automatic persistence: `FileSessionManager`, `S3SessionManager`, and the abstract `RepositorySessionManager` + `SessionRepository` interface for custom backends. When attached to an `Agent`, it auto-saves on init, every `MessageAddedEvent`, every invocation completion, and after multi-agent node transitions.

For our project this is **opt-out**. The backend (FastAPI + Postgres) is the canonical source of truth; the runner is ephemeral. Three options, in order of preference:

1. **Just don't pass a `session_manager`** — agent is fully stateless per spawn. The backend rehydrates conversation history into the runner by pre-pushing prior messages on startup. Simplest; recommended for the PoC.
2. Implement a `SessionRepository` that proxies to our REST API. Adds a round-trip on every save; useful only if we want Strands' multi-agent orchestrator state persisted too.
3. Use `FileSessionManager` against a per-runner temp dir and discard on shutdown. Useless — duplicates state we already own.

Pick option 1 for the PoC.

---

## 8. Hooks → OTel

Strands ships native OTel via `from strands.telemetry import StrandsTelemetry`; call `StrandsTelemetry().setup_otlp_exporter()` (or `.setup_console_exporter()`) once at boot and the SDK auto-spans agent invocations, model calls, and tool calls. The docs explicitly call out spans for "agents, cycles, LLM invocations, and tools" but **do not explicitly state that hook events themselves emit spans** — they emit on the same underlying lifecycle points, so for our purposes tool-call hooks and tool-call spans are effectively one and the same signal. Confirm empirically once a collector is wired.

---

## 9. Gotchas

- **Bedrock is the default model**. With no `model=` kwarg, `Agent()` tries us-west-2 Bedrock + Claude 4 Sonnet. We *will* hit auth errors on dev machines without AWS creds. Always pass `model=AnthropicModel(...)` explicitly.
- **`agentskills` is not on PyPI.** Pin it in `pyproject.toml` as a git dependency (`uv add 'agentskills @ git+https://github.com/aws-samples/sample-strands-agents-agentskills'`) or vendor it. Internal package name is `strands_agentskills` 0.2.0 even though you `import agentskills`.
- **MCP `stdio_client`/`StdioServerParameters` come from the upstream `mcp` package**, not from `strands`. Add `mcp` to `pyproject.toml` even though `strands-agents` pulls it transitively — explicit > implicit.
- **`agent.hooks.add_callback(EventType, fn)`** is the registry-style API on the live agent. The earlier `agent.add_hook(...)` shorthand surfaced in some doc pages; the canonical path is the registry. Use `add_callback`.
- **Sub-agent-as-tool fires `BeforeToolCallEvent`**, not `BeforeMultiAgentInvocationEvent` — the multi-agent events are for graph/swarm orchestrators, not for agents-as-tools. Good for us (one hook covers everything), but note the asymmetry if we ever migrate to graph.
- **Hooks are sync or async, but in-process only.** Webhooks to the backend = `httpx` call inside the callback. There is no SDK-level webhook dispatcher.
- **`MCPClient` context-manager exit** kills the subprocess. If you return the agent from a function and exit the `with` block, the next tool call will fail. Keep the `with kloc_mcp:` scope wrapping the entire session lifetime.

---

## 10. Open questions

- **Does `StrandsTelemetry` instrument hook events as their own spans, or only the underlying tool/model calls?** Needs empirical check — wire a `setup_console_exporter()` and observe.
- **`AnthropicModel(model_id="claude-sonnet-4-6")` vs `claude-sonnet-4-5-20250929`** — Strands docs example uses `claude-sonnet-4-6`. Verify the alias resolves on the Anthropic API at install time; pin to a dated id (`claude-sonnet-4-6-20260301` or whichever is current) for reproducibility before we ship.
- **`HookProvider.register_hooks` signature** — needs confirmation from `src/strands/hooks/registry.py` once we're writing the production policy plugin (PoC uses the inline `add_callback` form).
- **Lifecycle of `MCPClient` across async loops** — does `list_tools_sync()` block the loop? If yes, we'll want `list_tools_async()` for the FastAPI handler. Verify at integration time.
- **`agentskills` 0.2.0 stability** — the repo is `sample-*` (AWS sample, not a supported library). Watch for breaking changes; consider vendoring if the API churns. Repo last updated 2026-05-12.
- **Sub-agent streaming**: when `summarizer` runs as a tool, do its intermediate events propagate to the orchestrator's stream, or only the final return value? Matters for AG-UI streaming UX. Test with `agent.stream_async()`.

---

### Sources

- https://github.com/strands-agents/sdk-python (README, release v1.39.0 — 2026-05-08)
- https://strandsagents.com/docs/user-guide/concepts/tools/mcp-tools/
- https://strandsagents.com/docs/user-guide/concepts/multi-agent/agents-as-tools/
- https://strandsagents.com/docs/user-guide/concepts/agents/session-management/
- https://strandsagents.com/docs/user-guide/concepts/model-providers/anthropic/
- https://strandsagents.com/docs/user-guide/observability-evaluation/traces/
- https://raw.githubusercontent.com/strands-agents/sdk-python/main/src/strands/hooks/__init__.py
- https://raw.githubusercontent.com/strands-agents/sdk-python/main/src/strands/hooks/events.py
- https://github.com/aws-samples/sample-strands-agents-agentskills (`setup.py` → `strands_agentskills==0.2.0`, examples 1 & 3)
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview (Anthropic Agent Skills spec, progressive disclosure)
