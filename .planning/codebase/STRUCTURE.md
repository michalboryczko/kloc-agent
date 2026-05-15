# Codebase Structure

**Analysis Date:** 2026-05-15

## Directory Layout

```
kloc-agent/
├── src/                    # FastAPI backend (Python package)
│   ├── api/                # HTTP route handlers
│   │   ├── sessions.py     # Session CRUD
│   │   ├── stream.py       # SSE streaming endpoints
│   │   ├── internal.py     # Runner JSONL ingress + inbox
│   │   ├── webhooks.py     # HMAC hook receiver
│   │   ├── artifacts.py    # Artifact download endpoints
│   │   ├── stop.py         # Stop-run endpoint
│   │   └── health.py       # Health check
│   ├── db/                 # Database layer
│   │   ├── models.py       # ORM models + Pydantic schemas (HydrationPayload)
│   │   ├── engine.py       # SQLAlchemy async engine factory
│   │   ├── deps.py         # FastAPI dependency (get_session)
│   │   └── base.py         # DeclarativeBase
│   ├── repos/              # Repository pattern (one class per aggregate)
│   │   ├── sessions.py     # SessionRepo
│   │   ├── messages.py     # MessageRepo
│   │   ├── audit.py        # AuditRepo
│   │   ├── artifacts.py    # ArtifactRepo
│   │   └── boot.py         # Boot-time orphan message sweep
│   ├── runner_mgmt/        # Runner lifecycle management
│   │   ├── registry.py     # RunnerRegistry (per-session state map)
│   │   ├── docker_runner.py# DockerRunner (aiodocker concrete impl)
│   │   ├── protocol.py     # Runner + RunnerHandle protocols
│   │   ├── warm_idle.py    # WarmIdleManager (eviction timer)
│   │   ├── heartbeat.py    # HeartbeatWatcher (crash detector)
│   │   ├── hydrate.py      # HydrationPayload file write + volume mounts
│   │   └── sweeper.py      # Boot-time orphan container sweep
│   ├── streaming/          # In-process event routing
│   │   ├── event_bus.py    # EventBus (pub/sub keyed by session+run)
│   │   ├── execution_registry.py # Event ring for SSE cursor-replay
│   │   ├── agui_event_formatter.py # Normalize AG-UI events
│   │   ├── debounce.py     # TextDeltaDebouncer (batch DB writes)
│   │   └── sse.py          # SSE response factory
│   ├── hooks_audit/        # HMAC + tool-call policy
│   │   ├── verify_hmac.py  # HMAC-SHA256 signature + replay check
│   │   ├── policy.py       # Tool-call allow/deny policy
│   │   └── emit.py         # audit_emit closure factory
│   ├── storage/
│   │   └── s3.py           # S3/MinIO artifact upload helpers
│   ├── tools/              # (stub) reserved for future tools
│   ├── settings.py         # Pydantic-settings (lru_cache singleton)
│   └── main.py             # FastAPI app factory + lifespan
│
├── runner/                 # Runner Docker package
│   ├── __main__.py         # Runner entrypoint (asyncio.run(_run()))
│   ├── agent_factory.py    # Build Strands Agent + AG-UI wrapper
│   ├── model_factory.py    # LLM provider selection (Anthropic / Gemini)
│   ├── channel.py          # BackendChannel (JSONL out + inbox long-poll)
│   ├── mcp_clients.py      # Build MCP client list from hydration payload
│   └── hooks/
│       ├── audit.py        # AuditHookSender (Strands hook → HMAC POST)
│       └── utils.py        # Hook utility helpers
│
├── migrations/             # Alembic database migrations
│   ├── env.py              # Alembic environment (asyncpg async runner)
│   └── versions/
│       └── 2026_05_14_0001_init.py  # Initial schema
│
├── tests/                  # Test suite
│   ├── conftest.py         # Shared fixtures (app client, DB session, etc.)
│   ├── unit/               # Pure Python, no IO
│   ├── integration/        # Real Postgres + backend; runner/LLM mocked
│   ├── e2e/                # Full compose + real Docker runner
│   └── fixtures/           # Reusable test helpers
│       ├── sse_client.py
│       └── audit_events.py
│
├── frontend/               # Next.js 15 frontend
│   └── src/
│       ├── app/
│       │   ├── page.tsx            # Root page (session picker + CopilotKit shell)
│       │   ├── layout.tsx          # Root layout
│       │   └── api/
│       │       ├── copilotkit/route.ts   # CopilotRuntime handler
│       │       └── agent-proxy/route.ts  # AG-UI RunAgentInput proxy
│       ├── components/
│       │   ├── AgentBody.tsx       # useCoAgent state wiring
│       │   ├── ChatWindow.tsx      # Message thread display
│       │   ├── Composer.tsx        # Input box
│       │   └── ToolCallCard.tsx    # Tool call display card
│       ├── lib/
│       │   ├── api.ts              # Backend REST client (sessions, messages)
│       │   └── agui-http-agent.ts  # AG-UI HttpAgent helper
│       └── utils/
│           └── sseParser.ts        # SSE stream parser utility
│
├── skills/                 # Agent skill definitions (mounted into runner)
│   └── summarize-callgraph/
├── docs/                   # Research notes, specs, reviews
│   ├── specs/
│   ├── research/
│   └── reviews/
├── Dockerfile              # Backend image (Python 3.12)
├── docker-compose.yml      # Full stack (postgres, minio, backend, frontend, runner)
├── docker-compose.dev.yml  # Dev overrides
├── docker-compose.smoke.yml# Smoke test compose
├── pyproject.toml          # Python project + uv dependencies
├── alembic.ini             # Alembic config
└── Makefile                # Common dev targets
```

## Directory Purposes

**`src/api/`:**
- Purpose: FastAPI routers — one file per API surface area
- Contains: `APIRouter` instances; request/response Pydantic models defined inline; delegates immediately to repos or service layer
- Key files: `stream.py` (primary hot path), `internal.py` (runner ingress), `webhooks.py` (HMAC)

**`src/runner_mgmt/`:**
- Purpose: All runner container lifecycle logic — spawn, idle eviction, heartbeat, in-process inbox queue
- Contains: Protocol interface (`protocol.py`), concrete Docker implementation (`docker_runner.py`), registry (`registry.py`), supporting managers
- Key files: `registry.py` (core state), `docker_runner.py` (aiodocker impl)

**`src/streaming/`:**
- Purpose: In-process event routing between runner ingestion and SSE delivery; event durability for reconnects
- Contains: `EventBus` (pub/sub), `ExecutionRegistry` (event rings), SSE formatting
- Key files: `event_bus.py`, `execution_registry.py`

**`src/repos/`:**
- Purpose: Repository pattern; isolates all SQLAlchemy ORM logic; one class per aggregate root
- Key files: `messages.py` (streaming delta path), `audit.py` (audit log)

**`src/hooks_audit/`:**
- Purpose: Webhook security and observability — HMAC verification, tool-call policy, audit emission factory
- Key files: `verify_hmac.py`, `policy.py`

**`runner/`:**
- Purpose: Self-contained agent process running inside Docker; communicates with backend over HTTP only
- Contains: Strands agent construction, MCP client management, BackendChannel for outbound events and inbound inbox
- Key files: `__main__.py` (entrypoint), `agent_factory.py`, `channel.py`

**`migrations/versions/`:**
- Purpose: Alembic migration scripts; one file per migration
- Naming: `YYYY_MM_DD_NNNN_<description>.py`
- Generated: Yes (via `alembic revision`)
- Committed: Yes

**`skills/`:**
- Purpose: Agent skill definition files; bind-mounted read-only into runner containers via `kloc-skills` named Docker volume
- Generated: No (manually authored)

**`tests/fixtures/`:**
- Purpose: Reusable pytest helpers for SSE client and audit event construction; not conftest (not auto-loaded)

## Key File Locations

**Entry Points:**
- `src/main.py`: FastAPI app object (`app = create_app()`); `uvicorn src.main:app`
- `runner/__main__.py`: Runner entrypoint; `python -m runner` or Docker container CMD
- `frontend/src/app/page.tsx`: Next.js root page
- `migrations/env.py`: Alembic async migration runner

**Configuration:**
- `src/settings.py`: All backend settings as Pydantic `BaseSettings`; read from env / `.env`
- `.env.example`: Documents all required env vars (never read contents)
- `pyproject.toml`: Python deps, pytest config, build system
- `alembic.ini`: Alembic DB URL (overridden by env in `migrations/env.py`)
- `frontend/next.config.ts` (or `next.config.js`): Next.js config

**Core Logic:**
- `src/api/stream.py`: Primary user-request hot path (persist → spawn → subscribe → inbox put → SSE)
- `src/api/internal.py`: Runner event ingestion (`_dispatch_frame`)
- `src/runner_mgmt/registry.py`: Session-to-runner lifecycle management
- `src/streaming/event_bus.py`: In-process pub/sub; session+run keyed
- `src/db/models.py`: ORM models + `HydrationPayload` + `AuditEventType` vocabulary

**API Client (Frontend):**
- `frontend/src/lib/api.ts`: Typed REST client for backend sessions/messages API
- `frontend/src/app/api/agent-proxy/route.ts`: AG-UI translation layer

**Testing:**
- `tests/conftest.py`: Primary fixture file (app client, DB, mock runner)
- `tests/unit/`: Per-component unit tests, file named `test_<component>.py`
- `tests/integration/`: Tests requiring real Postgres
- `tests/e2e/`: Full-stack tests requiring Docker compose

## Naming Conventions

**Python files:**
- `snake_case.py` throughout
- Router files named by resource: `sessions.py`, `artifacts.py`, `stream.py`
- Service/manager files named by role: `registry.py`, `heartbeat.py`, `warm_idle.py`
- Test files: `test_<component>.py` mirroring the source module name

**Python classes:**
- `PascalCase`: `RunnerRegistry`, `MessageRepo`, `DockerRunner`, `BackendChannel`
- Protocol/interface suffix: `Runner` (Protocol), `RunnerHandle` (Protocol)
- Repo suffix: `SessionRepo`, `MessageRepo`, `AuditRepo`, `ArtifactRepo`
- Manager suffix: `WarmIdleManager`, `HeartbeatWatcher`

**Python functions:**
- `snake_case`; private helpers prefixed with `_`: `_dispatch_frame`, `_persist_events`, `_on_evict`
- Async functions are the norm; sync only for pure computation

**TypeScript files (frontend):**
- Route handlers: `route.ts` inside Next.js App Router directory
- Components: `PascalCase.tsx` — `AgentBody.tsx`, `ChatWindow.tsx`
- Utilities: `camelCase.ts` — `sseParser.ts`
- Library: `camelCase.ts` — `api.ts`, `agui-http-agent.ts`

**Migration files:**
- `YYYY_MM_DD_NNNN_<slug>.py` — e.g., `2026_05_14_0001_init.py`

## Where to Add New Code

**New API endpoint:**
- Add a new router file in `src/api/<resource>.py`
- Register in `src/main.py` via `app.include_router(..., prefix="/v1")`
- Add Pydantic request/response models in the same router file (inline, not a separate models file)

**New repository operation:**
- Add a method to the relevant class in `src/repos/<resource>.py`
- If a new aggregate root is needed, create `src/repos/<resource>.py` with a `<Name>Repo` class

**New DB table:**
- Add ORM model to `src/db/models.py` inheriting from `Base`
- Create new Alembic migration: `alembic revision --autogenerate -m "<description>"`
- Name migration file following `YYYY_MM_DD_NNNN_<slug>.py` pattern

**New audit event type:**
- Add the string literal to `AuditEventType` in `src/db/models.py` (locked vocabulary — coordinate with team)

**New runner capability:**
- Add logic to `runner/agent_factory.py` (tools) or `runner/__main__.py` (turn loop behavior)
- If touching the hydration contract, update `HydrationPayload` in `src/db/models.py`

**New streaming utility:**
- Add to `src/streaming/` (keep each file focused on one concern)

**New frontend component:**
- Add `PascalCase.tsx` to `frontend/src/components/`
- Import in `frontend/src/components/AgentBody.tsx` or `frontend/src/app/page.tsx`

**New backend REST client call (frontend):**
- Add typed function to `frontend/src/lib/api.ts`

**New unit test:**
- Add `tests/unit/test_<module>.py`
- Use `@pytest.mark.unit` marker

**New integration test:**
- Add `tests/integration/test_<feature>.py`
- Use `@pytest.mark.integration` marker; expects real Postgres via `conftest.py` fixtures

## Special Directories

**`.planning/codebase/`:**
- Purpose: Auto-generated codebase maps for GSD planning tools
- Generated: Yes (by `/gsd:map-codebase`)
- Committed: No (or at developer discretion)

**`.claude/progress/`:**
- Purpose: GSD phase progress tracking files
- Generated: Yes (by GSD commands)
- Committed: No

**`frontend/.next/`:**
- Purpose: Next.js build output
- Generated: Yes
- Committed: No

**`frontend/node_modules/`:**
- Purpose: Frontend npm dependencies
- Generated: Yes
- Committed: No

**`.venv/`:**
- Purpose: Python virtual environment (managed by uv)
- Generated: Yes
- Committed: No

**`skills/`:**
- Purpose: Agent skill files; copied into `kloc-skills` Docker named volume by `skills-init` service
- Generated: No (manually authored)
- Committed: Yes

---

*Structure analysis: 2026-05-15*
