# CLAUDE.md — kloc-agent

Project guide for Claude sessions. Read this once at session start; the
conventions below override default behavior.

## What this repo is

Self-hosted research-agent service. A FastAPI backend spawns per-session
Docker runner containers (one Strands `Agent` each) that talk to a
self-hosted `kloc-intelligence` MCP server, and stream AG-UI events back
to a Next.js frontend over SSE.

Stack:
- Python 3.12, `uv` for dependency management, `pytest` (`asyncio_mode=auto`).
- Backend: FastAPI + SQLAlchemy 2.x async + asyncpg + aioboto3 + Alembic.
- Runner: `strands` Agent SDK wrapped by `ag_ui_strands.StrandsAgent`,
  MCP over Streamable-HTTP to the operator-hosted `kloc-intelligence`.
- Transport: PGMQ inbox per session (see closed `docs/tasks/fix-runner-inbox/`).
- Frontend: Next.js + CopilotKit + AG-UI. React-markdown + remark-gfm for
  rich assistant prose.

## Hard invariants (do not violate)

1. **Single uvicorn worker.** In-process singletons (`event_bus`,
   `execution_registry`, `runner_registry`) are intentionally process-local.
   Don't introduce code paths that assume horizontal scaling.
2. **Settings is the single source for runtime config.** Read env via
   `from src.settings import get_settings; s = get_settings()`. The only
   sanctioned `os.environ` reads outside `src/settings.py` are the lazy
   reads inside `build_*_mount` helpers in `src/runner_mgmt/hydrate.py`
   (`KLOC_SKILLS_VOLUME` / `KLOC_AGENTS_VOLUME` / `KLOC_PROJECTS_VOLUME`),
   which match an existing pattern the verifier recognizes.
3. **Audit-event vocabulary is locked.** New `AuditEventType` Literal entries
   require BOTH a write site AND a downstream consumer; otherwise it is
   dead audit weight. Default: don't add. Reuse `tool_call` / `policy_decision` etc.
4. **HMAC constant-time comparison.** `BeforeToolCall` webhook auth must
   use `hmac.compare_digest`. Never loosen verification (`con.hmac-constant-time`).
5. **`runner/` may import from `src.db.models` ONLY for `HydrationPayload`.**
   The runner is a separate process in a separate container; do not
   accidentally cross the seam by reaching into backend modules.
6. **Don't commit. Don't push.** Without explicit user instruction in the
   chat. The `docs/tasks/run-pending-tasks.md` orchestration explicitly
   forbids it for dev/verifier subagents too.
7. **Don't touch `docs/tasks/tasks.md` unless you are the orchestrator.**
   The Status column is the orchestrator-owned state; dev/verifier
   subagents must leave it alone.

## Skills, agents, projects (operator-controlled trees)

Three read-only named volumes surface operator content into every runner.
Each is seeded from a repo-root directory by a one-shot `*-init` sidecar.

### `skills/` (kloc-skills -> /skills)

`SKILL.md` bodies are **inlined verbatim into the system prompt** at
agent build time (`runner/agent_factory.py:_load_skills_prompt`). We use
`strands_agentskills` ONLY for its discoverer (YAML-frontmatter validation
+ silent drop-with-warning of broken files). The upstream `AgentSkills`
plugin (with its `skills` tool for on-demand activation) is intentionally
NOT mounted. Rationale: skill set is small (≤ 12, < 50 KB), every turn
benefits from every skill, and we want the orchestrator to internalize
procedures before its first MCP call. Full discussion in
`docs/specs/agent-skills.md`.

Implication for editing:
- Don't write code that suggests the model can `file_read` a SKILL.md —
  the body is already in the prompt; doing so wastes a turn. The framing
  header in `_load_skills_prompt` enforces this contractually.
- Don't reference `agentskills.generate_skills_prompt` — that's the
  metadata-only path we removed. The presence of that import is a
  regression marker.
- Skills shipped: `biz-codebase-explorer` (analyst persona),
  `codebase-qa` (three-layer flow), `decompose` (Layer 1),
  `kloc-mcp` (Layer 2), `summarize-callgraph` (helper).

### `agents/` (kloc-agents -> /agents)

Subagents. Each `AGENT.md` (frontmatter `name` + `description`, body is
the subagent system prompt) is autoregistered by `runner/agents_loader.py`
and exposed as a delegated tool. Opaque to the user; prefer skills when
the user benefits from following along.

### `projects/` (kloc-projects -> /projects, RO)

Project source trees. Powers the runner-side `read_project_file` Strands
tool (`runner/tools/project_files.py`). Use cases:
- Byte-exact ranges (`kloc_source` / `kloc_chunks` lose alignment when
  the chunker collapses lines).
- Non-PHP files (YAML, XML, JSON, MD) that kloc-intelligence doesn't index.
- Files outside the indexed scope entirely.

Security boundary:
- `project_name` regex `^[a-z][a-z0-9-]{0,63}$` (mirrors `agents_loader._NAME_RE`).
- Path resolution: `Path(projects_dir, project_name, path).resolve(strict=True)`.
  `strict=True` is required — it makes symlinks to nonexistent targets raise.
- Containment check: `str(resolved).startswith(str(project_root_resolved) + os.sep)`.
  This is the symlink-escape defense; do NOT replace with `parent` walks.
- Binary heuristic: read first 8 KiB, attempt `.decode("utf-8")`. On
  `UnicodeDecodeError`, refuse.
- Size cap: `settings.tool_limits.read_project_file.max_bytes` (default
  256 KiB). Oversize -> deny with `tool_limit:file_too_large` + a
  `start_line` / `end_line` hint.

Operator workflow:
```bash
ln -s /home/me/code/paypo-kyc projects/kyc
docker compose down -v && docker compose up -d   # re-seeds via projects-init
```
`projects-init` uses `cp -R`, so symlinks are resolved into the volume
(frozen-snapshot semantics, not live source).

## USDL spec amendments

USDL Sections live under `docs/usdl/{behavior,topology,interfaces,composition}.xml`.
Tasks amend them in place — there is no overlay merge tool. Conventions:

- Use `Edit` (not `Write`) to preserve surrounding sections.
- All four files must remain parseable XML after every change. Run:
  ```bash
  python -c "import xml.etree.ElementTree as ET; [ET.parse(p) for p in ['docs/usdl/behavior.xml','docs/usdl/topology.xml','docs/usdl/interfaces.xml','docs/usdl/composition.xml']]"
  ```
- Placeholder tokens like `<runner_id>` inside `<description>` text must
  be entity-escaped (`&lt;runner_id&gt;`) — see existing instances in
  `docs/usdl/topology.xml`.
- Element ids are the verifier's contract surface; check for collisions
  with `grep -F` before adding.

## Tests

- Most of the suite is unit + integration; full suite runs as
  `pytest tests/ -q` (NOT `python -m pytest`).
- Integration tests requiring Postgres / MinIO are env-gated and skip
  cleanly when those services are down. Don't extend a graceful-skip
  block to cover an unrelated test — it masks regressions.
- `test_repos.py` uses `db_session.expunge_all()` after UPDATE + commit,
  before re-fetch, because `expire_on_commit=False` leaves the identity
  map cached. `expire_all()` would trigger an async lazy-reload and fail
  with `MissingGreenlet`.

## Code style

- No comments explaining WHAT — well-named code already does that.
- Comments are for WHY when non-obvious: a hidden constraint, a workaround,
  a security invariant near a `Path.resolve(strict=True)`. Keep them short.
- Never reference task ids, AC numbers, review rounds, or person names
  in comments.
- Don't introduce backwards-compatibility shims or `# removed` placeholder
  comments — delete cleanly.
- Don't add error handling for scenarios that can't happen; trust framework
  guarantees. Validate only at system boundaries (user input, external APIs).

## Pending-task orchestration

`docs/tasks/run-pending-tasks.md` is the dev/verifier loop. Key points if
you're the orchestrator:

- One subagent at a time (dev THEN verifier, never both — verifier needs
  `git diff` of the dev's work).
- `MAX_RETRIES=3` per task; escalate after.
- Verifier MUST run every `<VERIFICATION>` numbered check mechanically.
  **No `passed-infra-skipped`** without explicit operator override — the
  default policy is "bring up the infra and run the check".
- Every task that touches the frontend gets at least one Chrome MCP
  check (rebuild -> drive UI -> assert DOM via stable `data-test` selectors
  -> record GIF -> read console for regressions).
- The orchestrator updates `tasks.md` Status; dev/verifier do not touch it.
- Orchestrator-side artifacts (`tasks.md` row edits, sibling-task spec
  files) are NOT drive-bys against a per-task dev — exempt them in the
  verifier prompt or accept the false-positive failure with a documented
  override.

## Pointers

- `docs/architecture.md` — system / backend / runner ASCII diagrams.
- `docs/specs/kloc-agent-poc.md` — 26 ACs, 14 QA scenarios.
- `docs/specs/agent-skills.md` — skills-inlining rationale + migration criteria.
- `docs/specs/runner-project-files.md` — `kloc-projects` + `read_project_file`.
- `docs/specs/tool-result-size-limits.md` — `tool_limit:*` reason namespace,
  `KLOC_TOOL_LIMITS` shape.
- `docs/tasks/tasks.md` — task index (orchestrator-owned Status column).
- `docs/tasks/run-pending-tasks.md` — dev/verifier orchestration spec.
- `docs/tasks/fix-runner-inbox/` — closed multi-file predecessor (PGMQ
  inbox migration); do not re-implement.
