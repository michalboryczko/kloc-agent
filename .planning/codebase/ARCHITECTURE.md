<!-- refreshed: 2026-05-15 -->
# Architecture

**Analysis Date:** 2026-05-15

## System Overview

```text
┌───────────────────────────────────────────────────────────────────┐
│                    Next.js Frontend (port 3000)                   │
│  `frontend/src/app/page.tsx`  CopilotKit + session picker UI      │
├───────────────────────────┬───────────────────────────────────────┤
│  /api/copilotkit           │  /api/agent-proxy                     │
│  CopilotKit runtime route  │  AG-UI RunAgentInput builder         │
│  `frontend/src/app/api/   │  `frontend/src/app/api/agent-proxy/  │
│   copilotkit/route.ts`     │   route.ts`                          │
└───────────────────────────┴──────────────┬────────────────────────┘
                                           │ POST /v1/sessions/{id}/stream
                                           │ SSE response
                                           ▼
┌───────────────────────────────────────────────────────────────────┐
│                FastAPI Backend  `src/main.py`  (port 8000)        │
├────────────────┬──────────────┬───────────────┬───────────────────┤
│ sessions.py    │ stream.py    │ webhooks.py   │ internal.py       │
│ REST CRUD      │ SSE POST/GET │ HMAC receiver │ JSONL ingress     │
│ `src/api/`     │ `src/api/`   │ `src/api/`    │ `src/api/`        │
└───────┬────────┴──────┬───────┴───────┬───────┴────────┬──────────┘
        │               │               │                │
        ▼               ▼               ▼                ▼
┌───────────────────────────────────────────────────────────────────┐
│                    Service / Domain Layer                          │
│  RunnerRegistry     EventBus          ExecutionRegistry           │
│  `src/runner_mgmt/  `src/streaming/  `src/streaming/             │
│   registry.py`       event_bus.py`    execution_registry.py`      │
│                                                                   │
│  WarmIdleManager    HeartbeatWatcher  TextDeltaDebouncer          │
│  `src/runner_mgmt/  `src/runner_mgmt/ `src/streaming/            │
│   warm_idle.py`      heartbeat.py`    debounce.py`                │
└───────────────────────┬───────────────────────────────────────────┘
                        │ spawn/terminate/is_alive
                        ▼
┌───────────────────────────────────────────────────────────────────┐
│              Infrastructure / Runner Layer                         │
│  DockerRunner         Repos                DB / Storage            │
│  `src/runner_mgmt/    `src/repos/`         PostgreSQL (asyncpg)    │
│   docker_runner.py`   SessionRepo          MinIO (aioboto3)        │
│                       MessageRepo          `src/db/`               │
│                       AuditRepo            `src/storage/s3.py`     │
│                       ArtifactRepo                                 │
└───────────────────────┬───────────────────────────────────────────┘
                        │ Docker spawn via aiodocker
                        ▼
┌───────────────────────────────────────────────────────────────────┐
│              Runner Container  `runner/`  (isolated Docker env)   │
│  __main__.py  agent_factory.py  channel.py  mcp_clients.py       │
│  Strands Agent + ag_ui_strands wrapper + BackendChannel           │
│  Connects to kloc-intelligence over Streamable-HTTP MCP           │
└───────────────────────────────────────────────────────────────────┘
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

**Overall:** Event-driven micro-service with a sidecar Docker runner model

**Key Characteristics:**
- Backend (FastAPI) and runner (Docker container) communicate over HTTP only — no stdio, no shared memory
- AG-UI protocol (0.1.18) defines the event schema flowing from runner → backend → SSE client
- All durable state lives in Postgres; runner containers are stateless and disposable
- The frontend uses CopilotKit as a UI shell; a Next.js proxy translates CopilotKit's call format into AG-UI's `RunAgentInput`

## Layers

**API Layer:**
- Purpose: HTTP entrypoints; request validation; delegate to service/domain layer
- Location: `src/api/`
- Contains: FastAPI `APIRouter` modules — sessions, stream, webhooks, internal, artifacts, stop, health
- Depends on: Service layer, DB layer (via repos)
- Used by: External clients (frontend), runner containers

**Service / Domain Layer:**
- Purpose: Business logic — runner lifecycle, event routing, message persistence orchestration
- Location: `src/runner_mgmt/`, `src/streaming/`
- Contains: `RunnerRegistry`, `DockerRunner`, `EventBus`, `ExecutionRegistry`, `WarmIdleManager`, `HeartbeatWatcher`, `TextDeltaDebouncer`
- Depends on: DB layer, Docker API
- Used by: API layer

**DB / Repository Layer:**
- Purpose: All Postgres I/O; ORM models; S3 artifact storage
- Location: `src/db/`, `src/repos/`, `src/storage/`
- Contains: SQLAlchemy async session, ORM models (`Session`, `Message`, `AuditLog`, `ArtifactMetadata`), repository classes
- Depends on: PostgreSQL, MinIO
- Used by: API layer, service layer

**Hooks / Audit Layer:**
- Purpose: HMAC verification; tool-call policy decisions; audit event emission
- Location: `src/hooks_audit/`
- Contains: `verify_hmac.py`, `policy.py`, `emit.py`
- Depends on: DB layer (AuditRepo)
- Used by: `src/api/webhooks.py`

**Runner Package:**
- Purpose: Isolated agent execution environment inside Docker
- Location: `runner/`
- Contains: `__main__`, `channel.py`, `agent_factory.py`, `model_factory.py`, `mcp_clients.py`, `hooks/audit.py`
- Depends on: strands-agents, ag_ui_strands, kloc-intelligence MCP server (external), backend HTTP API
- Used by: Spawned by `DockerRunner` per session

**Frontend:**
- Purpose: Browser UI — session picker, chat sidebar, tool-call display
- Location: `frontend/src/`
- Contains: Next.js App Router pages/layouts, CopilotKit integration, Next.js API routes (agent-proxy, copilotkit)
- Depends on: Backend REST API, CopilotKit 1.52+, AG-UI client
- Used by: End users (browser)

## Data Flow

### Primary Request Path (User sends a message)

1. Browser sends message via CopilotKit sidebar → `POST /api/copilotkit` (`frontend/src/app/api/copilotkit/route.ts`)
2. CopilotRuntime calls `HttpAgent` → `POST /api/agent-proxy` (`frontend/src/app/api/agent-proxy/route.ts`)
3. Agent proxy builds `RunAgentInput` (UUID message IDs, threadId, runId) → `POST /v1/sessions/{id}/stream` to FastAPI
4. `stream_post` persists user message to Postgres first (Contract A invariant #1) (`src/api/stream.py`)
5. `stream_post` calls `RunnerRegistry.get_or_spawn(session_id, hydration_payload)` — spawns Docker container if none live
6. `event_bus.register(session_id, run_id)` creates subscriber queue BEFORE forwarding to runner (subscribe-before-publish)
7. `entry.inbox.put({"type":"user_message", ...})` enqueues the turn for the runner
8. `_persist_events` task fires asynchronously to tap the event bus and write assistant deltas to Postgres
9. SSE generator yields events from `event_bus.consume` back to the frontend as `text/event-stream`

### Runner Outbound Event Path

1. Runner calls `channel.emit(event)` → queues JSONL to outbound asyncio queue (`runner/channel.py`)
2. `_stream_outbound` sends JSONL lines in a long-lived chunked `POST /internal/sessions/{id}/events`
3. `ingest_runner_events` (`src/api/internal.py`) parses JSONL line-by-line → `_dispatch_frame`
4. `_dispatch_frame` routes: heartbeat → `RunnerRegistry.on_heartbeat_frame`; RUN_FINISHED → `on_run_finished` + publish; AG-UI events → `event_bus.publish(session_id, run_id, frame)`
5. `EventBus.publish` fans the event to all subscriber queues (SSE clients + `_persist_events` task)

### Runner Inbound Path (Backend → Runner)

1. Runner long-polls `GET /internal/sessions/{id}/inbox` every 25 s (`runner/channel.py:iter_inbound`)
2. `runner_inbox` (`src/api/internal.py`) delegates to `RunnerRegistry.inbox_get(session_id, timeout_s)`
3. When a turn queued by `stream_post` is waiting, registry returns it; runner receives the user message JSON

### Webhook Path (Runner → Backend tool-call hooks)

1. Strands `BeforeToolCallEvent` / `AfterToolCallEvent` fires in runner
2. `AuditHookSender` in `runner/hooks/audit.py` computes HMAC and `POST /v1/webhooks/runners/{runner_id}/events`
3. `receive_runner_event` (`src/api/webhooks.py`) verifies HMAC, consults `Policy.decide`, records to `audit_log`, updates in-flight tool-call tracking on registry
4. Returns `{decision: "allow"|"deny"}` within 2 s budget

**State Management:**
- Session/message/audit state is always Postgres (single source of truth)
- In-process state on `app.state`: `runner_registry`, `event_bus`, `active_run_by_session`, `pending_pre_run_started`
- Runner state is ephemeral inside the container; reconstructed from `HydrationPayload` at spawn time
- `ExecutionRegistry` holds in-memory event rings (up to 10 k events per run, TTL 5 min after completion)

## Key Abstractions

**`Runner` Protocol:**
- Purpose: Seam between `RunnerRegistry` and concrete implementation; enables test fakes
- Interface: `spawn`, `send_user_message`, `stream_events`, `terminate`, `is_alive`
- File: `src/runner_mgmt/protocol.py`
- Concrete impl: `src/runner_mgmt/docker_runner.py`

**`HydrationPayload`:**
- Purpose: Complete context for bootstrapping a runner container (session, history, model, MCP endpoints, secrets)
- Serialized to `/run/kloc/<runner_id>.json` inside a named Docker volume
- File: `src/db/models.py` (Pydantic model), re-exported from `src/runner_mgmt/protocol.py`

**`RegistryEntry`:**
- Purpose: Per-session slot in `RunnerRegistry` holding the container handle, inbox queue, warm-idle manager, heartbeat watcher
- File: `src/runner_mgmt/registry.py`

**`AuditEventType` Literal:**
- Purpose: Locked vocabulary of 12 audit event names enforced at write time
- File: `src/db/models.py`
- Values: `session_opened`, `session_closed`, `message_persisted`, `stream_orphaned`, `tool_call.started`, `tool_call.completed`, `tool_call.denied`, `tool_call.crashed`, `runner_spawned`, `runner_warm_idle_evicted`, `runner_heartbeat_lost`, `artifact_registered`

**AG-UI Event Bus:**
- Purpose: Decouple runner event ingestion from SSE delivery; support multiple simultaneous SSE subscribers
- Key pattern: `register` (creates queue) → trigger producer → `consume` (iterate queue) to close subscribe-before-publish race
- File: `src/streaming/event_bus.py`

## Entry Points

**Backend API server:**
- Location: `src/main.py` (`app = create_app()`)
- Triggers: `uvicorn src.main:app`
- Responsibilities: CORS, router mounting, lifespan (DB engine, S3 client, RunnerRegistry, DockerRunner, orphan sweeps)

**Runner container entrypoint:**
- Location: `runner/__main__.py` (`main()` → `asyncio.run(_run())`)
- Triggers: Docker container start (via `DockerRunner.spawn`)
- Responsibilities: Read hydration JSON, open MCP clients, build Strands agent, long-poll inbox loop, emit AG-UI events

**Database migrations:**
- Location: `migrations/env.py`, `migrations/versions/2026_05_14_0001_init.py`
- Triggers: `alembic upgrade head`

**Frontend dev server:**
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

**What happens:** `get_settings()` returns an `lru_cache`-pinned singleton. Any test that patches env vars must call `get_settings.cache_clear()` or use `Settings(...)` directly.
**Why it's wrong:** Stale settings leak between test cases if cache is not cleared.
**Do this instead:** Call `get_settings.cache_clear()` in test teardown, or inject a `Settings` override directly rather than relying on the env-patched singleton.

### Holding `_lock` Across Awaits in RunnerRegistry

**What happens:** `RunnerRegistry._lock` guards only the `_entries` dict for short critical sections. It is never held while awaiting a kill task.
**Why it's wrong:** The `_on_evict` callback re-acquires `_lock` to remove the entry; holding it in `get_or_spawn` during the spawn body would deadlock.
**Do this instead:** Release `_lock` before any `await` that could trigger a callback that re-acquires it. See `src/runner_mgmt/registry.py` "Concurrency model" comment.

### Direct `app.state` Access Without Null Guard

**What happens:** Several API handlers call `getattr(request.app.state, "runner_registry", None)`.
**Why it's wrong:** If lifespan fails partially, downstream handlers silently get `None` and return 503 or drop events.
**Do this instead:** Use `_get_runner_registry(request)` helper (already present in `src/api/stream.py`) which raises HTTP 503 on None, rather than inline `getattr` calls.

## Error Handling

**Strategy:** Fail fast on misconfiguration at boot; degrade gracefully on transient runtime errors

**Patterns:**
- Boot validates: provider API key presence (unless `KLOC_STUB_MODE=true`), DB reachability for orphan scan
- `DockerRunner` failures in "docker" mode hard-fail boot; in "stub" mode they log and continue
- `ClientDisconnect` in JSONL ingress is logged at INFO (runner reconnects); not treated as fatal
- Runner turn failures emit `RUN_ERROR` AG-UI event rather than crashing the container
- `_persist_events` task exceptions are caught by done-callback (`_log_persist_task_result`) so they surface in logs instead of silently swallowing
- HMAC webhook unknown runner_id → 401 before HMAC check (strict mode default)

## Cross-Cutting Concerns

**Logging:** Python `logging` under the `kloc_agent` logger tree (INFO forced at boot in `src/main.py`). Diagnostic lines in `src/api/internal.py` and `src/api/webhooks.py` write to `sys.stderr` directly to bypass uvicorn log-config filtering.
**Validation:** Pydantic v2 for all request/response models and settings. `HydrationPayload` is the schema contract for runner bootstrap.
**Authentication:** No user auth (PoC, single hardcoded analyst `analyst-poc`). Runner webhook auth via per-runner HMAC-SHA256 with 60 s replay window.
**Observability:** OpenTelemetry auto-instrumentation via `opentelemetry-distro[otlp]` + FastAPI/httpx/SQLAlchemy/asyncpg instrumentors. Runner uses `opentelemetry-instrument` entrypoint.

---

*Architecture analysis: 2026-05-15*
