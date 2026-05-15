# Technology Stack

**Analysis Date:** 2026-05-15

## Languages

**Primary:**
- Python 3.12 - Backend API server (`src/`), runner agent container (`runner/`), migrations (`migrations/`)
- TypeScript 5.6 - Next.js frontend (`frontend/src/`)

**Secondary:**
- SQL (PostgreSQL dialect) - Alembic migrations (`migrations/versions/`)

## Runtime

**Backend:**
- Python 3.12.x (constrained to `>=3.12,<3.13` in `pyproject.toml`)
- ASGI server: `uvicorn[standard]>=0.32` with `opentelemetry-instrument` wrapping the entrypoint

**Frontend:**
- Node.js 22 (Alpine-based Docker image in `frontend/Dockerfile`)

**Runner:**
- Python 3.12 inside a Docker container (`runner/Dockerfile`), shares the same `pyproject.toml` dependencies as the backend

## Package Managers

**Backend:**
- `uv` 0.5.4 (installed from `ghcr.io/astral-sh/uv:0.5.4` in `Dockerfile`)
- Lockfile: `uv.lock` (present and committed; `uv sync --frozen`)

**Frontend:**
- `npm` with `package-lock.json` (Node 22 image)
- Lockfile: `frontend/package-lock.json`

## Frameworks

**Backend:**
- `fastapi>=0.115` — REST API framework; app entry at `src/main.py`
- `pydantic>=2.9` + `pydantic-settings>=2.6` — data validation and settings management (`src/settings.py`)
- `sqlalchemy>=2.0` with asyncpg — async ORM; models in `src/db/models.py`
- `alembic>=1.14` — database migrations; config at `alembic.ini`, migrations at `migrations/`
- `strands-agents[anthropic,gemini]==1.39.0` — AWS Strands AI agent SDK; used in runner (`runner/agent_factory.py`, `runner/model_factory.py`)
- `ag-ui-protocol==0.1.18` — AG-UI wire protocol for agent streaming events
- `ag_ui_strands==0.1.8` — Strands adapter for AG-UI; wraps agent in `StrandsAgent`
- `mcp>=1.2,<2` — Model Context Protocol client SDK; used in `runner/mcp_clients.py`

**Frontend:**
- `next 16.0.8` — React meta-framework; App Router; standalone output mode
- `react ^19.2.1` + `react-dom ^19.2.1` — UI library
- `@copilotkit/react-core 1.56.5`, `@copilotkit/react-ui 1.56.5`, `@copilotkit/runtime 1.56.5` — CopilotKit chat UI and runtime wiring (`frontend/src/app/api/copilotkit/route.ts`, `frontend/src/app/page.tsx`)
- `@ag-ui/client 0.0.42` — AG-UI JS client (`HttpAgent`) for direct agent communication (`frontend/src/lib/agui-http-agent.ts`, `frontend/src/app/api/copilotkit/route.ts`)
- `zod ^3.23.8` — schema validation (available as dependency)

**Testing:**
- `pytest>=8.3` — test runner (`tests/`)
- `pytest-asyncio>=0.24` — async test support (configured `asyncio_mode = "auto"`)
- `pytest-cov>=5.0` — coverage reporting

**Build/Dev:**
- `hatchling` — Python wheel build backend
- `opentelemetry-distro[otlp]` + OTel instrumentation packages — auto-instrumentation for FastAPI, httpx, SQLAlchemy, asyncpg, logging

## Key Dependencies

**Critical:**
- `strands-agents[anthropic,gemini]==1.39.0` — core AI agent orchestration; pinned exactly; installed from PyPI with `[anthropic]` and `[gemini]` extras
- `strands_agentskills` — installed from a git SHA (`aws-samples/sample-strands-agents-agentskills@c5564fcd`) for skill discovery; not on PyPI; `hatch.metadata.allow-direct-references = true` required
- `ag-ui-protocol==0.1.18` — pinned; defines the wire format shared between backend and runner
- `ag_ui_strands==0.1.8` — pinned; bridges Strands agent to AG-UI event stream
- `asyncpg>=0.30` — PostgreSQL async driver; `greenlet>=3.0` required alongside it (SQLAlchemy async bridge)
- `aiodocker>=0.26` — Docker daemon control for runner container spawn (`src/runner_mgmt/docker_runner.py`)
- `aioboto3>=13.0` — async S3 client for MinIO artifact storage (`src/main.py`, `src/storage/s3.py`)

**Infrastructure:**
- `httpx>=0.27` — async HTTP client used by the runner channel (`runner/channel.py`)
- `python-multipart>=0.0.12` — multipart form support for FastAPI file upload endpoints

## Configuration

**Backend environment (read by `src/settings.py` via `pydantic-settings`):**
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

**Frontend environment:**
- `NEXT_PUBLIC_BACKEND_URL` — inlined into client bundle at build time; default `http://localhost:8000`
- `NEXT_PUBLIC_COPILOTKIT_AGENT_NAME` — agent name used by CopilotKit; default `kloc_agent`
- `BACKEND_URL` — server-side backend URL (used by `agent-proxy` route); default `http://localhost:8000`
- `COPILOTKIT_AGENT_NAME` — runtime agent name
- `NEXT_TELEMETRY_DISABLED=1` — telemetry disabled

**Build:**
- Backend: `pyproject.toml` + `uv.lock` + `alembic.ini`
- Frontend: `frontend/next.config.ts` (standalone output, `reactStrictMode: true`, server-external packages for pino/CopilotKit runtime)
- Frontend TypeScript: `frontend/tsconfig.json` (strict mode, `ES2022` target, path alias `@/*` → `./src/*`)
- Linting: `frontend/eslint.config.mjs` + `eslint-config-next 16.0.8`

## Platform Requirements

**Development:**
- Docker + Docker Compose (for Postgres, MinIO, runner containers)
- Python 3.12 + uv 0.5.4
- Node.js 22 + npm
- `/var/run/docker.sock` bind-mount required for runner spawn in `docker` mode

**Production:**
- Three-image Docker Compose stack: `backend` (Python 3.12-slim), `frontend` (Node 22-alpine standalone), runner containers (Python 3.12-slim spawned on-demand via aiodocker)
- External services: PostgreSQL 16 (or managed Postgres), MinIO (or S3-compatible), kloc-intelligence MCP server (separate Docker Compose stack: Neo4j + Qdrant + `mcp-server-http`)
- Optional OTel collector endpoint for distributed tracing

---

*Stack analysis: 2026-05-15*
