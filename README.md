# kloc-agent

Self-hosted research-agent service over the `kloc-intelligence` MCP.
Analysts ask natural-language questions about a PHP codebase from a web
chat and receive streamed, sourced answers.

PoC; see `docs/specs/kloc-agent-poc.md` for acceptance criteria and
`docs/specs/kloc-agent-poc-plan.md` for the implementation plan.

## Quick start

```
cp .env.example .env
# Secrets are read from your shell, not committed in .env. Export at minimum:
export GEMINI_API_KEY=...
# (or ANTHROPIC_API_KEY=... if you set LLM_PROVIDER=anthropic)
docker compose up -d
curl http://localhost:8002/healthz   # expect {"status":"ok"}
```

`.env` is the single source of truth for backend, frontend, and compose.
docker-compose loads it automatically from the project root; non-secret
values live there, secrets stay in your shell and are interpolated by
compose via `${VAR:-}` references. Do not put real keys in `.env`.

## Local development

```
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

bind-mounts `src/` for hot reload.

## Local URLs

| Service       | URL                       | Notes                                |
| ------------- | ------------------------- | ------------------------------------ |
| frontend      | http://localhost:3000     | Next.js                              |
| backend       | http://localhost:8002     | FastAPI (container port 8000 → host 8002) |
| postgres      | localhost:5432            | `kloc / changeme` (override in .env) |
| MinIO API     | http://localhost:9010     | S3 endpoint                          |
| MinIO console | http://localhost:9011     | `minioadmin / minioadmin`            |

## Tests

```
uv run pytest -m "not e2e"   # unit + integration
uv run pytest -m e2e         # full compose + real Anthropic + Docker runner
```

## Layout

```
src/        backend (FastAPI + SQLAlchemy + aioboto3)
runner/     in-container Strands Agent runtime
skills/     mounted read-only into runners (named volume kloc-skills); bodies inlined into the system prompt at agent build
agents/     mounted read-only into runners (named volume kloc-agents); autoregistered subagents
projects/   mounted read-only into runners (named volume kloc-projects); source for read_project_file
frontend/   Next.js + CopilotKit + AG-UI
migrations/ Alembic async
tests/      unit / integration / e2e
docs/       specs, architecture, research
```

## Skills, agents, projects

Three read-only named volumes surface operator-controlled trees into every
per-session runner. Each is seeded from the repo-root directory of the same
name by a one-shot `*-init` sidecar (`skills-init`, `agents-init`,
`projects-init`) on first boot. They are independent of each other:

| Tree        | Volume         | Mount   | Activation pattern |
| ----------- | -------------- | ------- | ------------------ |
| `skills/`   | kloc-skills    | /skills | Every `SKILL.md` body is **inlined verbatim** into the orchestrator's `system_prompt` at agent-build time (`runner/agent_factory.py:_load_skills_prompt`). The `strands_agentskills` package is used only as a discoverer (YAML-frontmatter validation, silent drop-with-warning of broken files); the upstream `AgentSkills` plugin is intentionally NOT mounted. The model sees the procedures up front and doesn't pay a `file_read` round-trip per skill activation. Migration criteria to the plugin pattern live in `docs/specs/agent-skills.md`. |
| `agents/`   | kloc-agents    | /agents | Each `AGENT.md` becomes one Strands subagent exposed as a delegated tool (`runner/agents_loader.py`). The model invokes them like any other tool; their internal work is opaque to the user. Frontmatter `name` + `description`; body = subagent system prompt. |
| `projects/` | kloc-projects  | /projects (RO) | Source for the runner-side `read_project_file(project_name, path, start_line?, end_line?)` Strands tool. Path resolution is strict (symlink-resilient, containment-checked against `/projects/{project_name}/`); oversize reads are denied with `tool_limit:file_too_large` + a re-plan hint. See `docs/specs/runner-project-files.md`. |

Skill set shipped today (`skills/`):

- `biz-codebase-explorer` — analyst persona + anti-hallucination guardrails
  (PL/EN, file:line citations mandatory, adversarial honesty).
- `codebase-qa` — three-layer procedure (decompose → retrieve → synthesize).
- `decompose` — Layer 1: structured plan + ambiguity / adversarial /
  runtime-data detection.
- `kloc-mcp` — Layer 2: kloc-intelligence MCP tool selection.
- `summarize-callgraph` — call-graph summarization helper.

Operator workflow:

```bash
# Add a new skill — drop a SKILL.md, re-seed, restart in-flight runners.
mkdir -p skills/my-new-skill && $EDITOR skills/my-new-skill/SKILL.md
docker compose down -v && docker compose up -d

# Add a project for read_project_file — symlink or copy a service tree.
ln -s /home/me/code/paypo-kyc projects/kyc          # frozen-snapshot semantics
docker compose down -v && docker compose up -d      # re-seeds the named volume
```

The `down -v` is required: `*-init` sidecars are one-shots whose seeded
volume content is sticky across recreates without an explicit volume drop.

## Caveats

- skills/ is bind-mounted **read-only** in the runner; mutating it requires
  re-seeding via `docker compose down -v && docker compose up -d`. The
  `kloc-skills` named-volume seed pattern sidesteps the
  bind-from-compose vs aiodocker path-resolution mismatch documented at
  `src/runner_mgmt/hydrate.py:126-130`.
- agents/ holds one `AGENT.md` per subagent (frontmatter `name` +
  `description`, body is the system prompt). The runner discovers them
  at startup via `runner/agents_loader.py` and exposes each as a
  delegated tool. Adding a subagent is drop-a-file + re-seed.
- projects/ is empty in git (just `.gitkeep`); operators populate it
  locally before `docker compose up`. `projects-init` resolves symlinks
  via `cp -R` (frozen snapshot, not live source). `read_project_file`
  enforces a 256 KiB cap by default (`KLOC_TOOL_LIMITS.read_project_file.max_bytes`);
  oversize reads surface a `ToolCallDenied` event with a `start_line`/`end_line`
  hint.
- Per-session Docker runners are spawned by the backend via aiodocker.
  They are NOT declared in docker-compose; the backend talks to the
  Docker socket on the host.
- AG-UI versions are pair-pinned: `ag-ui-protocol==0.1.18` (Python) ↔
  `@ag-ui/client==0.0.42` (frontend). Drift is a release-blocker (AC26).

## References

- `docs/specs/kloc-agent-poc.md` — 26 acceptance criteria, 14 QA scenarios.
- `docs/specs/kloc-agent-poc-plan.md` — File manifest, ownership table,
  4 interface contracts, phased tasks.
- `docs/specs/agent-skills.md` — Why SKILL.md bodies are inlined into the
  system prompt rather than gated behind the upstream `skills` tool, and
  the criteria under which we'd migrate to the plugin pattern.
- `docs/specs/runner-project-files.md` — `kloc-projects` volume +
  `read_project_file` tool design, including path-traversal / symlink /
  size defenses.
- `docs/architecture.md` — Three ASCII diagrams (system, backend, runner).
- `docs/research/04-persistence-storage.md` — Postgres schema + MinIO setup.
