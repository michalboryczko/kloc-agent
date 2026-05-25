# Reference: Agent Skills Loading

How `kloc-agent` exposes Markdown skills to the orchestrator agent, why
the loader deviates from the upstream Strands plugin pattern, and what
that deviation costs in observability.

This is a descriptive reference for the current state — not a change
proposal. For a comparison with the upstream design see
[Strands Agents — Skills plugin](https://strandsagents.com/docs/user-guide/concepts/plugins/skills/).

## Background — what a "skill" is

A skill is a self-contained Markdown procedure under `skills/<name>/SKILL.md`
with YAML frontmatter:

```markdown
---
name: codebase-qa
description: End-to-end workflow for answering business-analyst questions...
---

# Codebase QA

(procedure body, examples, hard rules, …)
```

Skills currently in this repo (`skills/`):

- `biz-codebase-explorer` — analyst-facing persona + hard rules
- `codebase-qa` — three-layer decompose → retrieve → synthesize procedure
- `decompose` — Layer 1 question decomposition
- `kloc-mcp` — Layer 2 retrieval via `kloc-intelligence` MCP tools
- `summarize-callgraph` — call-graph summarization helper

The dependency `strands_agentskills` is pulled from a pinned git revision
in `pyproject.toml:28` and `uv.lock:1697-1699`.

## The Strands-native pattern (for reference)

The upstream `AgentSkills` plugin runs in three phases:

1. **Init.** Plugin injects `<available_skills>` XML into the system
   prompt with **metadata only** — `<name>` + `<description>` per skill.
2. **Activation.** When the agent decides a skill is relevant, it
   invokes a `skills` tool with the skill name. The tool returns the
   full SKILL.md body plus any resource files in the skill directory.
3. **State.** Activated skills are recorded under
   `agent_skills` on agent state; `plugin.get_activated_skills(agent)`
   exposes the list for session persistence and post-run inspection.

Trade-off the upstream picks: lean context window + on-demand bodies +
one extra round-trip per activation + first-class activation telemetry.

## What `kloc-agent` does instead

The runner uses `strands_agentskills` **only as a discovery library**.
It does not register `AgentSkills` as a plugin on the `Agent`, so:

- the `skills` tool is not exposed,
- no `agent_skills` state key is populated,
- there is no `get_activated_skills(agent)` accessor.

Instead, every SKILL.md body is read from disk and **inlined verbatim**
into the orchestrator's system prompt at agent-build time.

### Loading flow

`runner/agent_factory.py:_load_skills_prompt` (lines 111-170):

1. **Discover.** Call `agentskills.discover_skills(skills_dir)` — walks
   `skills_dir/*/SKILL.md`, parses YAML frontmatter, returns a list of
   `Skill(name, description, path)`. Files with broken frontmatter are
   dropped with a `log.warning`; the runner keeps going.
2. **Inline.** For each skill, read the full file body via
   `Path(skill.path).read_text(encoding="utf-8")`, strip the leading
   `---…---` frontmatter block so name/description don't appear twice,
   and wrap the result in:
   ```xml
   <skill>
     <name>{skill.name}</name>
     <description>{skill.description}</description>
     <body>
   {content}
     </body>
   </skill>
   ```
3. **Frame.** Prepend a header that explicitly tells the model the bodies
   are already loaded and a `file_read` of any SKILL.md is wasteful:
   > "The following skills are inlined in full. Treat each one as if
   > you had already read it — do NOT issue a file_read just to load a
   > SKILL.md whose body is already shown below. Apply the skill when
   > its description matches the user's question."
4. **Concatenate.** Append the `<available_skills>` block to the
   operator-authored `base_prompt` from
   `src/api/stream.py:400-441`, and pass the result as
   `Agent(system_prompt=...)` (`runner/agent_factory.py:245-249`).

The `skills_dir` defaults to `/skills` inside the runner container
(`src/api/stream.py:467`, `src/db/models.py:338`). The backend seeds the
mount from a `kloc-agents` named volume (`src/db/models.py:341`).

### Why the deviation

The motivating comment at `runner/agent_factory.py:112-123` is intentional:

> For our skill set (≤ a dozen, total body well under 50 KB) the extra
> round-trip costs more latency than the tokens save, and we WANT the
> orchestrator to internalize the skills before it issues its first MCP
> call. So we use the `agentskills` discoverer (it validates YAML
> frontmatter and silently drops broken files with a warning) but
> inline the full body ourselves.

In short: the orchestrator's first decision (decompose → retrieve)
depends on skill content; gating the body behind a tool call adds a
turn before any real work happens.

## Symmetry with subagents

Subagents (`agents/<name>/AGENT.md`) are loaded by
`runner/agents_loader.py` (note the docstring at line 6: "Mirrors the
shape of `agentskills.discovery` so operators reason about one loader
pattern across `/skills` and `/agents`"). The split is:

| | Skills | Subagents |
|---|---|---|
| Loader | `runner/agent_factory.py:_load_skills_prompt` | `runner/agents_loader.py:build_subagents` |
| Source dir | `/skills` (`skills_dir`) | `/agents` (`agents_dir`) |
| What the LLM sees | Bodies inlined into the system prompt | Tool entries (one per agent) with description = AGENT.md's `description` |
| Activation | None — already loaded | Standard tool call on the subagent tool |
| Visibility to user | Inlined rules influence the visible answer | Opaque — subagent's internal work is not surfaced |
| Telemetry | None per-skill (see below) | Standard `BeforeToolCall`/`AfterToolCall` audit rows |

This is the trade-off the base prompt encodes in `src/api/stream.py:418-421`:

> subagents (if any are listed among your tools) — opaque specialist
> personas; their internal work is NOT visible to the user, so prefer
> skills when the user benefits from following along.

## Observability gap — "did the agent use skill X?"

Because skills are not invoked through a tool, the audit trail
(`runner/hooks/audit.py:108,253` → HMAC-signed POST to
`/v1/webhooks/runners/{runner_id}/events`) records nothing about skill
application. The Strands-native answer
(`plugin.get_activated_skills(agent)`) is also unavailable: the plugin
isn't mounted.

Available signals, in decreasing order of directness:

1. **Tool-call signature in the audit trail.** A skill like `codebase-qa`
   prescribes a characteristic MCP sequence (e.g. `kloc_resolve` →
   `kloc_search` → `kloc_context` → `kloc_source`). The pattern appearing
   in `tool_call` audit rows for a session is circumstantial evidence
   the skill was applied; a bare `kloc_explain` without prior resolve/
   search suggests it wasn't.
2. **Output artifacts the skill mandates.** `biz-codebase-explorer`
   requires `file:line` citations, source-language matching, and
   "z kodu wynika X" framing. Their presence in the final message is
   observable; their absence is evidence of skipping.
3. **Startup discovery logs.** `_load_skills_prompt` emits
   `skills.agentskills_not_installed`, `skills.discover_failed`, and
   `skills.body_read_failed` (`runner/agent_factory.py:127,134,144`).
   This tells you which skills were *available* at startup — not which
   were *applied* on a given run.

The `runner/hooks/utils.py:resolve_tool_call` helper anticipates a
`skill_executor` wrapper tool from upstream repo 2 (see
`docs/implementation-plan.md` task D15) and would unwrap it for audit if
present — but the wrapper itself is task E6 in the implementation plan
and is deliberately deferred.

## When to switch to the plugin pattern

The current inlining is the right call while:

- the skill set fits comfortably in the system prompt (current total
  body well under 50 KB),
- every orchestrator turn is expected to potentially benefit from every
  skill, and
- per-skill activation telemetry is not required.

The plugin pattern starts paying off when:

- skill bodies grow or the count crosses ~20 (context-budget pressure),
- some skills are situational and rarely apply (token waste per turn),
- operators want first-class "which skills did this session activate?"
  telemetry without inferring it from MCP-tool patterns, or
- skills start carrying resource files (`SKILL.md` + sibling assets)
  that benefit from on-demand fetch.

Migration is mechanical: register `AgentSkills(skills=...)` as a plugin
on the `Agent` constructed in `runner/agent_factory.py:build_agent`,
drop the manual inlining in `_load_skills_prompt`, and let each
activation flow through the existing `BeforeToolCall` →
`AfterToolCall` audit hook with `tool_name="skills"`. Post-run callers
can additionally read `plugin.get_activated_skills(agent)` for an
authoritative list.

## File index

- `runner/agent_factory.py:111-170` — `_load_skills_prompt` (inline loader)
- `runner/agent_factory.py:214-249` — `build_agent` (composes base prompt + skills block + tools)
- `runner/agents_loader.py` — subagent discovery (parallel pattern for `/agents`)
- `runner/hooks/utils.py` — `resolve_tool_call` (would unwrap `skill_executor` if E6 ever lands)
- `runner/hooks/audit.py:108,253` — Before/After tool-call audit POSTs
- `src/api/stream.py:400-441` — operator-authored `base_prompt`
- `src/api/stream.py:467` — `skills_dir="/skills"` payload field
- `src/db/models.py:326-341` — `HydrationPayload` (`system_prompt`, `skills_dir`)
- `pyproject.toml:28` / `uv.lock:1697-1699` — pinned `strands_agentskills` source
