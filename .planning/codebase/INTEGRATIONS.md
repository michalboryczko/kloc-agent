# External Integrations

**Analysis Date:** 2026-05-15

## APIs & External Services

**AI / LLM Providers (runner-side, selected by `LLM_PROVIDER` env):**
- **Anthropic Claude** — via `strands.models.anthropic.AnthropicModel`; installed with `strands-agents[anthropic]` extras
  - SDK/Client: `anthropic` (transitive of `strands-agents[anthropic]`)
  - Auth env: `ANTHROPIC_API_KEY`
  - Optional proxy: `ANTHROPIC_BASE_URL`
  - Used in: `runner/model_factory.py`
- **Google Gemini** — via `strands.models.gemini.GeminiModel`; installed with `strands-agents[gemini]` extras; default provider
  - SDK/Client: `google-genai` (transitive of `strands-agents[gemini]`)
  - Auth env: `GEMINI_API_KEY` or `GOOGLE_API_KEY`
  - Used in: `runner/model_factory.py`
- **OpenRouter** (stubbed) — `NotImplementedError` raised if `LLM_PROVIDER=openrouter`
- **AWS Bedrock** (stubbed) — `NotImplementedError` raised if `LLM_PROVIDER=bedrock`

**MCP (Model Context Protocol) — kloc-intelligence:**
- kloc-intelligence is an **operator-managed, separate Docker Compose stack** (Neo4j + Qdrant + `mcp-server-http`) running outside kloc-agent
- Connection: Streamable-HTTP transport (MCP 2025-03-26 spec) via `mcp.client.streamable_http.streamablehttp_client`
- URL env: `KLOC_MCP_URL` (default `http://host.docker.internal:8765/mcp`)
- Also supports stdio MCP child-process specs via `mcp.stdio_client` + `StdioServerParameters`
- Client construction: `runner/mcp_clients.py` — discriminates `McpHttpEndpoint` (has `url`) vs `McpStdioEndpoint` (has `command`/`args`/`env`)
- Endpoint specs delivered via `HydrationPayload.mcp_endpoints` (defined in `src/db/models.py`)
- MCP tools exposed to the Strands agent at runtime: kloc-intelligence's code-intelligence graph queries (Neo4j-backed) and vector search (Qdrant-backed)

**Strands AgentSkills:**
- `strands_agentskills` — installed from git SHA `aws-samples/sample-strands-agents-agentskills@c5564fcd`
- Used in `runner/agent_factory.py`: `agentskills.discover_skills(skills_dir)` + `agentskills.generate_skills_prompt(skills)`
- Skills directory mounted at `/skills` in runner containers; seeded via Docker named volume `kloc-skills` from `./skills/` on the host

## Data Storage

**Primary Database:**
- PostgreSQL 16 (Alpine image in `docker-compose.yml`)
- Tables: `sessions`, `messages`, `audit_log`, `artifact_metadata` (defined in `src/db/models.py`)
- ORM: SQLAlchemy 2.0 async with `asyncpg` driver
- Connection env: `DATABASE_URL` (asyncpg URL: `postgresql+asyncpg://...`)
- Default: `postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent`
- Schema management: Alembic (`alembic.ini`, `migrations/versions/2026_05_14_0001_init.py`)
- SessionLocal: `src/db/engine.py`; dependency injection via `src/db/deps.py`

**File / Object Storage:**
- MinIO (S3-compatible) — artifact storage for runner-produced files
- Client: `aioboto3>=13.0` with botocore S3v4 signature + path addressing style
- Lifespan-managed on `app.state.s3` in `src/main.py`
- Helpers: `src/storage/s3.py` (`upload_bytes`, `presigned_get`, `artifact_object_key`)
- Object key scheme: `sessions/{session_id}/artifacts/{artifact_id}/{filename}`
- Presigned URLs generated with 900s expiry for download
- Env vars: `MINIO_ENDPOINT_URL`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_USE_SSL`, `ARTIFACT_BUCKET`
- Default bucket: `kloc-agent-artifacts-dev`
- Docker named volume: `minio-data`; bucket created by `mc-init` init container

**Caching:**
- None — no Redis or in-memory cache layer beyond Python `lru_cache` for `get_settings()`

## Authentication & Identity

**User Auth:**
- Not implemented — hardcoded `HARDCODED_ANALYST_ID = "analyst-poc"` in `src/api/sessions.py`; auth is explicitly out of scope for PoC

**Runner → Backend Webhook Auth:**
- HMAC-SHA256 per-runner secrets; implementation in `src/hooks_audit/verify_hmac.py`
- Canonicalization: `signing_input = f"{ts}.{body}"`; `Authorization: HMAC <base64>`
- Replay window: 60 seconds (checked in `verify_hmac_signature`)
- Per-runner secrets minted at spawn; bootstrap fallback secret from `KLOC_HOOK_SECRET` env
- Verification used in `src/api/webhooks.py`

## Observability

**Distributed Tracing / Metrics:**
- OpenTelemetry auto-instrumentation via `opentelemetry-distro[otlp]`
- Instruments: FastAPI, httpx, SQLAlchemy, asyncpg, logging
- Both backend and runner containers wrap their entrypoints with `opentelemetry-instrument` binary
- Exporter: configurable via env — `OTEL_TRACES_EXPORTER` (default `console`), `OTEL_METRICS_EXPORTER` (default `console`)
- OTLP endpoint: `OTEL_EXPORTER_OTLP_ENDPOINT` (empty by default = no remote collector)
- Backend service name: `OTEL_SERVICE_NAME` (default `kloc-agent`)
- Runner service name: `kloc-agent-runner` (baked into `runner/Dockerfile` ENV)
- Runner OTEL env vars forwarded from backend process at spawn time in `src/runner_mgmt/docker_runner.py`

**Logging:**
- Python stdlib `logging`; `kloc_agent` logger tree forced to INFO in `src/main.py`
- Structured extra fields (e.g., `session_id`, `runner_id`) on log records throughout
- B-DIAG diagnostic lines written directly to stderr via `sys.stderr` to bypass uvicorn log filtering

**Error Tracking:**
- None (no Sentry or similar)

## Docker / Container Management

**Docker Daemon (DooD — Docker-outside-of-Docker):**
- Backend manages per-session runner containers on the host daemon via `aiodocker>=0.26`
- Client: `aiodocker.Docker()` instance inside `DockerRunner` (`src/runner_mgmt/docker_runner.py`)
- Socket: `/var/run/docker.sock` bind-mounted into backend container
- Named Docker network `kloc` (explicit `name: kloc` in `docker-compose.yml`) so aiodocker-spawned runners join the same bridge
- Runner containers labeled `kloc.role=runner`, `kloc.session_id`, `kloc.runner_id`
- Resource limits: 1 GiB memory, 2 vCPUs (2×10⁹ NanoCpus), 256 PIDs, `RestartPolicy: no`
- Hydration data shared via Docker named volume `kloc-hydration`; skills shared via `kloc-skills`

## CI/CD & Deployment

**Containerization:**
- Backend: `Dockerfile` (Python 3.12-slim, uv 0.5.4, multi-stage via single stage)
- Frontend: `frontend/Dockerfile` (Node 22-alpine, 3-stage: deps → build → run; standalone Next.js output)
- Runner: `runner/Dockerfile` (Python 3.12-slim, uv 0.5.4, same pyproject.toml as backend)
- Orchestration: `docker-compose.yml` (production), `docker-compose.dev.yml` (dev overrides), `docker-compose.smoke.yml` (smoke tests)

**CI Pipeline:**
- Not detected in repo (no GitHub Actions, CircleCI, or similar config found)

**Hosting:**
- Self-hosted via Docker Compose on operator infrastructure
- No cloud-managed hosting detected

## Webhooks & Callbacks

**Incoming (runner → backend):**
- `POST /v1/hooks/tool_call` — runner audit webhook; HMAC-signed; handled in `src/api/webhooks.py`
  - Reports: `tool_call.started`, `tool_call.completed`, `tool_call.denied`, `tool_call.crashed`, `artifact_registered`
  - Verification: per-runner secret via `src/hooks_audit/verify_hmac.py`
  - Policy enforcement: `src/hooks_audit/policy.py`

**Outgoing (backend → runner, internal HTTP transport):**
- `POST /internal/sessions/{id}/events` — JSONL event stream from runner to backend; not a webhook but chunked HTTP ingress; handled in `src/api/internal.py`
- `GET /internal/sessions/{id}/inbox` — long-poll (≤25s) for user messages from backend to runner; handled in `src/api/internal.py`

**Frontend → Backend:**
- `POST /v1/sessions` — create session
- `GET /v1/sessions` — list sessions
- `GET /v1/sessions/{id}` — session detail
- `GET /v1/sessions/{id}/messages` — message history (cursor-paginated)
- `POST /v1/sessions/{id}/stream` — AG-UI RunAgentInput → SSE event stream
- `POST /v1/sessions/{id}/close` — close session
- `POST /v1/sessions/{id}/stop` — stop running agent
- `GET /v1/artifacts/{id}` — artifact presigned URL redirect
- `GET /healthz` — health check

## CopilotKit Runtime Integration

**Frontend → CopilotKit → AG-UI → Backend path:**
- Browser renders `CopilotKit` + `CopilotSidebar` components (React, `frontend/src/app/page.tsx`)
- CopilotKit runtime POSTs to Next.js route `POST /api/copilotkit` (`frontend/src/app/api/copilotkit/route.ts`)
- `CopilotRuntime` wraps an `HttpAgent` from `@ag-ui/client` pointing at `/api/agent-proxy`
- `agent-proxy` route (`frontend/src/app/api/agent-proxy/route.ts`) translates CopilotKit body shape → AG-UI `RunAgentInput` and proxies SSE to FastAPI `/v1/sessions/{id}/stream`
- `ExperimentalEmptyAdapter` used (no cloud CopilotKit backend; fully self-hosted)

---

*Integration audit: 2026-05-15*
