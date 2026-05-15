# kloc-agent — docs

A hosted research-agent service over `kloc-intelligence`. Analysts open a web
chat, ask natural-language questions about a PHP code base, and receive
streamed, sourced answers. Strands SDK runs in per-session Docker containers;
the FastAPI backend persists everything before forwarding; CopilotKit + AG-UI
on the frontend. Single chat is a single `session_id`; the runner container
stays warm for 60 s between messages and is rehydrated from Postgres on the
next message after eviction.

## Where to start

Read in this order:

1. **[`poc.md`](../poc.md)** — original project brief. What we're building and why.
2. **[`investigation.md`](investigation.md)** — synthesis of all research. Locked
   decisions, module layout, vertical PoC slice, risk inventory.
3. **[`architecture.md`](architecture.md)** — three ASCII diagrams:
   system-level → backend internals → runner / agent code organization.
   Includes the warm-idle state machine and same-chat-rehydrate flow.
4. **[`implementation-plan.md`](implementation-plan.md)** — checkbox plan
   organized by track (A → H) with references back into the briefs. No code,
   just what to build and where to read.

Drill into the research briefs as needed:

| # | Topic | File |
|---|------|------|
| 01 | Strands SDK — minimum-viable usage | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) |
| 02 | Backend + AG-UI streaming (critical path) | [`research/02-backend-agui.md`](research/02-backend-agui.md) |
| 03 | Runner management & isolation (Docker mode) | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) |
| 04 | Persistence (Postgres) + storage (MinIO) | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) |
| 05 | Reference projects — infra extraction | [`research/05-reference-projects.md`](research/05-reference-projects.md) |

## Quick-reference: locked stack

- **Backend**: Python 3.12 + FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic
- **Frontend**: Next.js 16 + CopilotKit 1.52.1 + `@ag-ui/client` 0.0.42
- **Agent**: Strands Agents 1.39.0 + `ag_ui_strands` 0.1.8 + `strands_agentskills` 0.2.0 (git)
- **Runner**: Docker only (aiodocker) — one mode for PoC and prod
- **Persistence**: Postgres 16 + MinIO (S3 API)
- **Wire protocol**: AG-UI 0.1.18 over SSE
- **MCP**: stdio JSON-RPC 2.0 child of the runner (talks to `kloc-intelligence`)
- **Observability**: OTel auto-instrumentation (`opentelemetry-instrument`)
- **Env management**: `pydantic-settings`

## Status

Phase 1 (investigation) complete. Phase 2 (implementation plan) in
[`implementation-plan.md`](implementation-plan.md). No code written yet.

## Conventions

- Source layout: `src/` (backend package) + `runner/` (code that runs inside
  each Docker container) + `frontend/` (Next.js). See
  [`investigation.md`](investigation.md) §3 for the full module tree.
- Skills live in `./skills/<name>/SKILL.md`, bind-mounted read-only into
  every runner container at `/skills`.
- Settings file (this doc): everything user-facing or decision-relevant goes
  in `investigation.md` or `architecture.md`. Research briefs are append-only
  historical record — corrections go into `investigation.md` first.
