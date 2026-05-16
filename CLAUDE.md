<!-- GSD:project-start source:PROJECT.md -->
## Project

**kloc-agent**

`kloc-agent` is a single-operator AI agent orchestration system: a FastAPI backend spawns disposable Docker runner containers per session, streams Strands-Agents events back over AG-UI to a Next.js/CopilotKit browser UI, and persists every message + audit event to Postgres. Runners connect to an external `kloc-intelligence` MCP server for the actual codebase-knowledge tools. The product today is a PoC for a single analyst exploring an indexed PHP codebase; this milestone hardens it to demo-stable for internal beta use.

**Core Value:** A single analyst can have a live, resumable, audit-complete agent conversation against an indexed codebase — and trust that every event, message, and tool call is reliably persisted, ordered correctly, and never silently dropped.

### Constraints

- **Tech stack (backend)**: Python 3.12, FastAPI ≥ 0.115, SQLAlchemy 2.x async + asyncpg, `strands-agents==1.39.0`, `ag-ui-protocol==0.1.18`, `ag_ui_strands==0.1.8`, uv 0.5.4 — locked, no version upgrades in this milestone
- **Tech stack (frontend)**: Next.js 16.0.8, React 19.2.1, CopilotKit 1.56.5, `@ag-ui/client 0.0.42`, TypeScript 5.6 strict — locked; UI work uses what ships with these versions
- **Runtime mode**: Docker is the only runner mode after ISS-12; `stub` is removed. Local-dev parity is no longer a goal.
- **Single uvicorn worker**: in-process singletons (`event_bus`, `execution_registry`, `runner_registry`) remain process-local; do not introduce horizontal-scaling assumptions
- **Test policy**: bug fixes (ISS-01..06, FE-SEC) ship with a regression test that would have caught them; cleanups (ISS-09, ISS-10, ISS-13, UI-P6) do not require new tests
- **Comment policy** (from ISS-13): default to no comments; comments must explain a non-obvious *why* and stand alone without project context; never name people, plan sections, ACs, review rounds, or describe history
- **Atomicity**: each phase commits its artifacts; mechanical sweeps (ISS-13, UI-P6) land as single behaviour-neutral PRs
- **Working tree**: starting baseline is commit `13fd93f57` on `master`; main branch is `main` (per `git status` snapshot)
- **Scope discipline**: only findings from `docs/reviews/code-review/`, `docs/reviews/ui-design-review/`, `docs/reviews/frontend/` are in scope; nothing else
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.12 - Backend API server (`src/`), runner agent container (`runner/`), migrations (`migrations/`)
- TypeScript 5.6 - Next.js frontend (`frontend/src/`)
- SQL (PostgreSQL dialect) - Alembic migrations (`migrations/versions/`)
## Runtime
- Python 3.12.x (constrained to `>=3.12,<3.13` in `pyproject.toml`)
- ASGI server: `uvicorn[standard]>=0.32` with `opentelemetry-instrument` wrapping the entrypoint
- Node.js 22 (Alpine-based Docker image in `frontend/Dockerfile`)
- Python 3.12 inside a Docker container (`runner/Dockerfile`), shares the same `pyproject.toml` dependencies as the backend
## Package Managers
- `uv` 0.5.4 (installed from `ghcr.io/astral-sh/uv:0.5.4` in `Dockerfile`)
- Lockfile: `uv.lock` (present and committed; `uv sync --frozen`)
- `npm` with `package-lock.json` (Node 22 image)
- Lockfile: `frontend/package-lock.json`
## Frameworks
- `fastapi>=0.115` — REST API framework; app entry at `src/main.py`
- `pydantic>=2.9` + `pydantic-settings>=2.6` — data validation and settings management (`src/settings.py`)
- `sqlalchemy>=2.0` with asyncpg — async ORM; models in `src/db/models.py`
- `alembic>=1.14` — database migrations; config at `alembic.ini`, migrations at `migrations/`
- `strands-agents[anthropic,gemini]==1.39.0` — AWS Strands AI agent SDK; used in runner (`runner/agent_factory.py`, `runner/model_factory.py`)
- `ag-ui-protocol==0.1.18` — AG-UI wire protocol for agent streaming events
- `ag_ui_strands==0.1.8` — Strands adapter for AG-UI; wraps agent in `StrandsAgent`
- `mcp>=1.2,<2` — Model Context Protocol client SDK; used in `runner/mcp_clients.py`
- `next 16.0.8` — React meta-framework; App Router; standalone output mode
- `react ^19.2.1` + `react-dom ^19.2.1` — UI library
- `@copilotkit/react-core 1.56.5`, `@copilotkit/react-ui 1.56.5`, `@copilotkit/runtime 1.56.5` — CopilotKit chat UI and runtime wiring (`frontend/src/app/api/copilotkit/route.ts`, `frontend/src/app/page.tsx`)
- `@ag-ui/client 0.0.42` — AG-UI JS client (`HttpAgent`) for direct agent communication (`frontend/src/lib/agui-http-agent.ts`, `frontend/src/app/api/copilotkit/route.ts`)
- `zod ^3.23.8` — schema validation (available as dependency)
- `pytest>=8.3` — test runner (`tests/`)
- `pytest-asyncio>=0.24` — async test support (configured `asyncio_mode = "auto"`)
- `pytest-cov>=5.0` — coverage reporting
- `hatchling` — Python wheel build backend
- `opentelemetry-distro[otlp]` + OTel instrumentation packages — auto-instrumentation for FastAPI, httpx, SQLAlchemy, asyncpg, logging
## Key Dependencies
- `strands-agents[anthropic,gemini]==1.39.0` — core AI agent orchestration; pinned exactly; installed from PyPI with `[anthropic]` and `[gemini]` extras
- `strands_agentskills` — installed from a git SHA (`aws-samples/sample-strands-agents-agentskills@c5564fcd`) for skill discovery; not on PyPI; `hatch.metadata.allow-direct-references = true` required
- `ag-ui-protocol==0.1.18` — pinned; defines the wire format shared between backend and runner
- `ag_ui_strands==0.1.8` — pinned; bridges Strands agent to AG-UI event stream
- `asyncpg>=0.30` — PostgreSQL async driver; `greenlet>=3.0` required alongside it (SQLAlchemy async bridge)
- `aiodocker>=0.26` — Docker daemon control for runner container spawn (`src/runner_mgmt/docker_runner.py`)
- `aioboto3>=13.0` — async S3 client for MinIO artifact storage (`src/main.py`, `src/storage/s3.py`)
- `httpx>=0.27` — async HTTP client used by the runner channel (`runner/channel.py`)
- `python-multipart>=0.0.12` — multipart form support for FastAPI file upload endpoints
## Configuration
- `DATABASE_URL` — asyncpg connection string; default `postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent`
- `MINIO_ENDPOINT_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_USE_SSL`, `ARTIFACT_BUCKET` — S3/MinIO storage
- `LLM_PROVIDER` — `gemini` (default) or `anthropic`
- `GEMINI_API_KEY` — required when `LLM_PROVIDER=gemini`
- `ANTHROPIC_API_KEY` — required when `LLM_PROVIDER=anthropic`
- `ANTHROPIC_BASE_URL` — optional proxy override
- `RUNNER_IMAGE_TAG`, `RUNNER_WARM_IDLE_S`, `RUNNER_HEARTBEAT_TIMEOUT_S` — runner lifecycle
- `KLOC_RUNNER_MODE` — `docker` (default) or `stub` (CI/no-docker mode)
- `KLOC_MCP_URL` — Streamable-HTTP MCP endpoint for kloc-intelligence
- `KLOC_HOOK_SECRET` — HMAC bootstrap secret for runner→backend webhooks
- `KLOC_DENY_TOOLS` — comma-separated tool deny list
- `KLOC_CORS_ALLOW_ORIGINS` — comma-separated CORS allowed origins
- `KLOC_STUB_MODE` — skip provider key validation (tests/CI)
- `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_TRACES_EXPORTER`, `OTEL_METRICS_EXPORTER` — OpenTelemetry
- Example: `.env.example` at repo root
- `NEXT_PUBLIC_BACKEND_URL` — inlined into client bundle at build time; default `http://localhost:8000`
- `NEXT_PUBLIC_COPILOTKIT_AGENT_NAME` — agent name used by CopilotKit; default `kloc_agent`
- `BACKEND_URL` — server-side backend URL (used by `agent-proxy` route); default `http://localhost:8000`
- `COPILOTKIT_AGENT_NAME` — runtime agent name
- `NEXT_TELEMETRY_DISABLED=1` — telemetry disabled
- Backend: `pyproject.toml` + `uv.lock` + `alembic.ini`
- Frontend: `frontend/next.config.ts` (standalone output, `reactStrictMode: true`, server-external packages for pino/CopilotKit runtime)
- Frontend TypeScript: `frontend/tsconfig.json` (strict mode, `ES2022` target, path alias `@/*` → `./src/*`)
- Linting: `frontend/eslint.config.mjs` + `eslint-config-next 16.0.8`
## Platform Requirements
- Docker + Docker Compose (for Postgres, MinIO, runner containers)
- Python 3.12 + uv 0.5.4
- Node.js 22 + npm
- `/var/run/docker.sock` bind-mount required for runner spawn in `docker` mode
- Three-image Docker Compose stack: `backend` (Python 3.12-slim), `frontend` (Node 22-alpine standalone), runner containers (Python 3.12-slim spawned on-demand via aiodocker)
- External services: PostgreSQL 16 (or managed Postgres), MinIO (or S3-compatible), kloc-intelligence MCP server (separate Docker Compose stack: Neo4j + Qdrant + `mcp-server-http`)
- Optional OTel collector endpoint for distributed tracing
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Python modules: `snake_case.py` — e.g., `agent_factory.py`, `verify_hmac.py`, `event_bus.py`
- Python packages: `snake_case/` directories with `__init__.py`
- TypeScript/TSX files: `PascalCase.tsx` for components (`ChatWindow.tsx`, `ToolCallCard.tsx`), `camelCase.ts` for utilities/lib (`agui-http-agent.ts`, `api.ts`, `sseParser.ts`)
- Test files: `test_<module_name>.py` — mirrors `src/` module path exactly
- Python: `PascalCase` — `RunnerRegistry`, `SessionRepo`, `WarmIdleManager`, `HeartbeatWatcher`
- Protocol classes: `PascalCase` with `Protocol` suffix — `Runner`, `RunnerHandle` (runtime_checkable)
- Pydantic models: `PascalCase` — `HydrationPayload`, `CreateSessionBody`, `PostMessageResponse`
- SQLAlchemy ORM: `PascalCase` — `Session`, `Message`, `AuditLog`, `ArtifactMetadata`
- Dataclasses: `PascalCase` — `RegistryEntry`
- Python: `snake_case` — `get_settings()`, `create_engine_for_settings()`, `verify_hmac_signature()`
- Private helpers: `_snake_case` — `_diag()`, `_split_cors_allow_origins()`, `_validate_provider_key()`
- Module-level constants: `UPPER_SNAKE_CASE` — `REPLAY_WINDOW_MS`, `HARDCODED_ANALYST_ID`, `FLUSH_BYTES`
- TypeScript: `camelCase` for functions — `createSession()`, `listSessions()`, `jsonOrThrow()`
- Python: `snake_case` — `session_id`, `runner_id`, `warm_idle_s`
- Logger name: always `log = logging.getLogger(__name__)` at module level (except `src/main.py` and named-subsystem loggers)
- TypeScript: `camelCase`
- Python `Literal` types used for constrained strings — `AuditEventType`, `LlmProvider`, `MessageRole`
- Python `TypeAlias` pattern: `AuditEmitFn = Callable[[str, dict], Awaitable[None]]` at module level
## Module Docstrings
## Code Style
- Python: no explicit formatter config found (no `pyproject.toml [tool.ruff]` / `[tool.black]` sections); PEP 8 conventions observed throughout
- TypeScript: no Prettier config; ESLint via `eslint.config.mjs` using `next/core-web-vitals` + `next/typescript`
- TypeScript strict mode: `"strict": true` in `tsconfig.json`
- Python: `# noqa: <code>` suppressions used sparingly (`F401` for intentional re-exports in `__init__.py`)
- Python: `# type: ignore[<code>]` used for aiodocker optional-import pattern and duck-typed attributes
- TypeScript: ESLint `next/core-web-vitals` + `next/typescript`
## Import Organization
- `@/*` maps to `./src/*` in `tsconfig.json`
- Components imported as `import { ChatWindow } from "@/components/ChatWindow"`
## Error Handling
- Raise `HTTPException` directly with appropriate status codes
- Pattern: check precondition → raise if violated → proceed with happy path
- Catch broad exceptions with `except Exception as e:` + `logger.exception(...)` for defensive paths
- Use specific exception types for expected operational errors (e.g. `OperationalError`, `InterfaceError`)
- Non-critical boot failures use `logger.info()` with skip reason; critical failures re-raise
- Return `bool` never raise on bad input — `verify_hmac_signature` catches all exceptions and returns `False`
- Used with `from exc` where cause matters: `raise SSEParseError(...) from exc`
## Logging
- Module-level: `log = logging.getLogger(__name__)` (most modules)
- Named subsystems: `log = logging.getLogger("kloc_agent.webhooks")` and `log = logging.getLogger("kloc_agent.internal")` for webhook/internal API modules
- Root app: `logger = logging.getLogger("kloc_agent")` in `src/main.py`
- `log.info()` — operational events (boot steps, runner spawn, eviction)
- `log.exception()` — caught exceptions that should surface (always includes traceback via `exc_info=True` implicitly)
- Avoid `log.debug()` in hot paths (not observed in codebase)
## Comments
- Every non-trivial decision has an inline comment referencing plan sections, AC numbers, or reviewer IDs
- Guard clauses explaining WHY a check exists (not just what it does)
- Concurrency invariants documented where locking occurs (see `src/runner_mgmt/registry.py` module docstring explaining `_lock` discipline)
- AC numbers: `(AC15)`, `(AC24)` etc. appear in comments throughout
- Phase references: `Phase 1.A7`, `dev-2 CR`, `Track H` etc.
- Reviewer comments: `# Reviewer-2 C1 follow-up:` in test files
## Function Design
- Pydantic models returned from API handlers (typed by `response_model=`)
- `None` returned explicitly from `204 No Content` endpoints
- `bool` from verification/check functions
- `AsyncIterator` from SSE/streaming generators
## Module Design
- `__init__.py` files re-export public API symbols: `from src.runner_mgmt.registry import RunnerRegistry  # noqa: F401`
- `Final` constants in `tests/fixtures/audit_events.py` serve as the canonical audit event vocabulary
- Minimal: `src/runner_mgmt/__init__.py` exports `RunnerRegistry` and `sweeper`
- `src/api/__init__.py` is empty (routes imported directly in `src/main.py`)
## Python-Specific Conventions
- `BaseSettings` from `pydantic_settings` for settings
- `Field(default_factory=...)` for mutable defaults
- `model_validator(mode="after")` for cross-field validation
- `field_validator(..., mode="before")` for coercion
- `Mapped[T]` / `mapped_column(...)` typed ORM style throughout `src/db/models.py`
- `async with engine.begin() as conn:` for connection-level operations
- `await session.flush()` (not `commit()`) in repos; commit happens at the API layer
- Dependency injection via `Depends(get_session)` for DB sessions
- Router tags used: `tags=["sessions"]`, `tags=["webhooks"]`
- `app.state.*` for lifespan-owned singletons (engine, S3, RunnerRegistry)
## TypeScript / Next.js Conventions
- `jsonOrThrow<T>(res)` helper used throughout `src/lib/api.ts` for uniform error handling
- `BROWSER_BACKEND_URL` from `process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000"`
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
```
## Component Responsibilities
| Component | Responsibility | File |
|-----------|----------------|------|
| `create_app` | FastAPI app factory, CORS, router mounting, lifespan | `src/main.py` |
| `sessions` router | Session CRUD: create, list, get, post message, close | `src/api/sessions.py` |
| `stream` router | SSE POST (persist + forward to runner) and GET (replay+tail) | `src/api/stream.py` |
| `internal` router | JSONL ingress from runners; long-poll inbox for runners | `src/api/internal.py` |
| `webhooks` router | HMAC-signed hook receiver for BeforeToolCall / AfterToolCall / ArtifactRegistered | `src/api/webhooks.py` |
| `RunnerRegistry` | Per-session runner lifecycle: spawn, warm-idle, heartbeat, inbox queue | `src/runner_mgmt/registry.py` |
| `DockerRunner` | Concrete `Runner` Protocol: aiodocker container create/start/stop/delete | `src/runner_mgmt/docker_runner.py` |
| `EventBus` | In-process pub/sub keyed by `(session_id, run_id)`: runner events → SSE subscribers | `src/streaming/event_bus.py` |
| `ExecutionRegistry` | Bounded event ring per `(session_id, run_id)` for SSE cursor-replay reconnect | `src/streaming/execution_registry.py` |
| `WarmIdleManager` | Countdown timer; evicts idle runner containers after `runner_warm_idle_s` | `src/runner_mgmt/warm_idle.py` |
| `HeartbeatWatcher` | Kills runner entry if no heartbeat arrives within `runner_heartbeat_timeout_s` | `src/runner_mgmt/heartbeat.py` |
| `TextDeltaDebouncer` | Batches streaming text deltas (256 chars / 250 ms) before DB write | `src/streaming/debounce.py` |
| `MessageRepo` | Append, append_delta, finalize messages; server-side concat | `src/repos/messages.py` |
| `SessionRepo` | Session CRUD | `src/repos/sessions.py` |
| `AuditRepo` | Append audit_log rows; last_state_snapshot lookup | `src/repos/audit.py` |
| `ArtifactRepo` | Idempotent artifact_metadata insert (ON CONFLICT DO NOTHING) | `src/repos/artifacts.py` |
| `runner.__main__` | Runner entrypoint: read hydration, build agent, long-poll inbox loop | `runner/__main__.py` |
| `BackendChannel` | Runner-side HTTP transport: JSONL outbound stream + inbox long-poll | `runner/channel.py` |
| `build_agent` | Constructs Strands `Agent` with MCP tools + audit hooks | `runner/agent_factory.py` |
| `AuditHookSender` | Runner-side Strands hook callbacks → HMAC-signed webhook POSTs | `runner/hooks/audit.py` |
| `Settings` | Pydantic-settings; validated at boot; lru_cache singleton | `src/settings.py` |
| Next.js `/api/agent-proxy` | Translates CopilotKit call → AG-UI `RunAgentInput` → backend SSE forward | `frontend/src/app/api/agent-proxy/route.ts` |
| Next.js `/api/copilotkit` | CopilotRuntime handler wired to the agent-proxy HttpAgent | `frontend/src/app/api/copilotkit/route.ts` |
## Pattern Overview
- Backend (FastAPI) and runner (Docker container) communicate over HTTP only — no stdio, no shared memory
- AG-UI protocol (0.1.18) defines the event schema flowing from runner → backend → SSE client
- All durable state lives in Postgres; runner containers are stateless and disposable
- The frontend uses CopilotKit as a UI shell; a Next.js proxy translates CopilotKit's call format into AG-UI's `RunAgentInput`
## Layers
- Purpose: HTTP entrypoints; request validation; delegate to service/domain layer
- Location: `src/api/`
- Contains: FastAPI `APIRouter` modules — sessions, stream, webhooks, internal, artifacts, stop, health
- Depends on: Service layer, DB layer (via repos)
- Used by: External clients (frontend), runner containers
- Purpose: Business logic — runner lifecycle, event routing, message persistence orchestration
- Location: `src/runner_mgmt/`, `src/streaming/`
- Contains: `RunnerRegistry`, `DockerRunner`, `EventBus`, `ExecutionRegistry`, `WarmIdleManager`, `HeartbeatWatcher`, `TextDeltaDebouncer`
- Depends on: DB layer, Docker API
- Used by: API layer
- Purpose: All Postgres I/O; ORM models; S3 artifact storage
- Location: `src/db/`, `src/repos/`, `src/storage/`
- Contains: SQLAlchemy async session, ORM models (`Session`, `Message`, `AuditLog`, `ArtifactMetadata`), repository classes
- Depends on: PostgreSQL, MinIO
- Used by: API layer, service layer
- Purpose: HMAC verification; tool-call policy decisions; audit event emission
- Location: `src/hooks_audit/`
- Contains: `verify_hmac.py`, `policy.py`, `emit.py`
- Depends on: DB layer (AuditRepo)
- Used by: `src/api/webhooks.py`
- Purpose: Isolated agent execution environment inside Docker
- Location: `runner/`
- Contains: `__main__`, `channel.py`, `agent_factory.py`, `model_factory.py`, `mcp_clients.py`, `hooks/audit.py`
- Depends on: strands-agents, ag_ui_strands, kloc-intelligence MCP server (external), backend HTTP API
- Used by: Spawned by `DockerRunner` per session
- Purpose: Browser UI — session picker, chat sidebar, tool-call display
- Location: `frontend/src/`
- Contains: Next.js App Router pages/layouts, CopilotKit integration, Next.js API routes (agent-proxy, copilotkit)
- Depends on: Backend REST API, CopilotKit 1.52+, AG-UI client
- Used by: End users (browser)
## Data Flow
### Primary Request Path (User sends a message)
### Runner Outbound Event Path
### Runner Inbound Path (Backend → Runner)
### Webhook Path (Runner → Backend tool-call hooks)
- Session/message/audit state is always Postgres (single source of truth)
- In-process state on `app.state`: `runner_registry`, `event_bus`, `active_run_by_session`, `pending_pre_run_started`
- Runner state is ephemeral inside the container; reconstructed from `HydrationPayload` at spawn time
- `ExecutionRegistry` holds in-memory event rings (up to 10 k events per run, TTL 5 min after completion)
## Key Abstractions
- Purpose: Seam between `RunnerRegistry` and concrete implementation; enables test fakes
- Interface: `spawn`, `send_user_message`, `stream_events`, `terminate`, `is_alive`
- File: `src/runner_mgmt/protocol.py`
- Concrete impl: `src/runner_mgmt/docker_runner.py`
- Purpose: Complete context for bootstrapping a runner container (session, history, model, MCP endpoints, secrets)
- Serialized to `/run/kloc/<runner_id>.json` inside a named Docker volume
- File: `src/db/models.py` (Pydantic model), re-exported from `src/runner_mgmt/protocol.py`
- Purpose: Per-session slot in `RunnerRegistry` holding the container handle, inbox queue, warm-idle manager, heartbeat watcher
- File: `src/runner_mgmt/registry.py`
- Purpose: Locked vocabulary of 12 audit event names enforced at write time
- File: `src/db/models.py`
- Values: `session_opened`, `session_closed`, `message_persisted`, `stream_orphaned`, `tool_call.started`, `tool_call.completed`, `tool_call.denied`, `tool_call.crashed`, `runner_spawned`, `runner_warm_idle_evicted`, `runner_heartbeat_lost`, `artifact_registered`
- Purpose: Decouple runner event ingestion from SSE delivery; support multiple simultaneous SSE subscribers
- Key pattern: `register` (creates queue) → trigger producer → `consume` (iterate queue) to close subscribe-before-publish race
- File: `src/streaming/event_bus.py`
## Entry Points
- Location: `src/main.py` (`app = create_app()`)
- Triggers: `uvicorn src.main:app`
- Responsibilities: CORS, router mounting, lifespan (DB engine, S3 client, RunnerRegistry, DockerRunner, orphan sweeps)
- Location: `runner/__main__.py` (`main()` → `asyncio.run(_run())`)
- Triggers: Docker container start (via `DockerRunner.spawn`)
- Responsibilities: Read hydration JSON, open MCP clients, build Strands agent, long-poll inbox loop, emit AG-UI events
- Location: `migrations/env.py`, `migrations/versions/2026_05_14_0001_init.py`
- Triggers: `alembic upgrade head`
- Location: `frontend/src/app/page.tsx` (root page), `frontend/src/app/layout.tsx`
- Triggers: `next dev` / `next start`
## Architectural Constraints
- **Threading:** Single uvicorn worker (asyncio event loop). All in-process dicts on `app.state` (`active_run_by_session`, `pending_pre_run_started`) are safe only in single-worker mode. Multi-worker requires a shared store.
- **Global state:** `event_bus` singleton (`src/streaming/event_bus.py`), `execution_registry` singleton (`src/streaming/execution_registry.py`), `get_settings()` lru_cache singleton (`src/settings.py`), `get_sessionmaker()` (module-level in `src/db/engine.py`)
- **Circular imports:** None detected. Runner package (`runner/`) imports from `src.db.models` for `HydrationPayload` via try/except fallback; `src/runner_mgmt/protocol.py` re-exports with TYPE_CHECKING guard to avoid circular at runtime.
- **Runner isolation:** Runners have no direct DB access — all persistence goes through the backend HTTP API (webhook POSTs, inbox long-poll)
- **HMAC secret per runner:** Each spawned runner receives a unique `runner_secret` in its `HydrationPayload`; the bootstrap secret (`kloc_hook_secret`) is only a fallback for dev/tests (`allow_hmac_fallback=True`)
## Anti-Patterns
### Global Settings Mutation
### Holding `_lock` Across Awaits in RunnerRegistry
### Direct `app.state` Access Without Null Guard
## Error Handling
- Boot validates: provider API key presence (unless `KLOC_STUB_MODE=true`), DB reachability for orphan scan
- `DockerRunner` failures in "docker" mode hard-fail boot; in "stub" mode they log and continue
- `ClientDisconnect` in JSONL ingress is logged at INFO (runner reconnects); not treated as fatal
- Runner turn failures emit `RUN_ERROR` AG-UI event rather than crashing the container
- `_persist_events` task exceptions are caught by done-callback (`_log_persist_task_result`) so they surface in logs instead of silently swallowing
- HMAC webhook unknown runner_id → 401 before HMAC check (strict mode default)
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

| Skill | Description | Path |
|-------|-------------|------|
| vercel-react-best-practices | React and Next.js performance optimization guidelines from Vercel Engineering. This skill should be used when writing, reviewing, or refactoring React/Next.js code to ensure optimal performance patterns. Triggers on tasks involving React components, Next.js pages, data fetching, bundle optimization, or performance improvements. | `.agents/skills/vercel-react-best-practices/SKILL.md` |
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
