# 05 — Reference Projects: Infra Patterns to Lift for kloc-agent

> **Scope:** Mine 4 reference repos for **infrastructure** patterns we can copy directly into `kloc-agent` (Python + FastAPI + Strands + Next.js + CopilotKit + AG-UI + MCP + Anthropic Skills). Strands internals, persistence design, runner mgmt, and frontend-protocol design are covered in `01-strands-minimal.md`, `03-runner-mgmt.md`, `04-persistence-storage.md`.
>
> **Out of scope:** AWS Bedrock AgentCore specifics. Where a repo depends on AgentCore (managed runtime, AgentCore Memory, AgentCore Gateway, Cognito, AgentCore Registry, SSM, Secrets Manager, etc.), only the **shape** of the pattern is captured — never the AWS plumbing.

The repos:

| # | Repo | Why it matters |
|---|------|---------------|
| 1 | [`aws-samples/sample-fraud-investigation-assistance-using-aws-bedrock-strandsagents-mcp`](https://github.com/aws-samples/sample-fraud-investigation-assistance-using-aws-bedrock-strandsagents-mcp) | Strands + MCP + multi-agent + Streamlit chat UI. Closest topology to our research-agent. |
| 2 | [`aws-samples/sample-strands-agent-with-agentcore`](https://github.com/aws-samples/sample-strands-agent-with-agentcore) | **The mother lode.** Production-shaped FastAPI + Next.js + AG-UI + Strands + Skills + hooks + MCP + session mgmt + execution registry. |
| 3 | [`aws-samples/sample-AIOPS-agent-bedrock-strandsagents`](https://github.com/aws-samples/sample-AIOPS-agent-bedrock-strandsagents) | Clean, minimal FastAPI + file-per-route + file-per-agent layout. Good "starter skeleton" reference. |
| 4 | [`strands-agents/samples`](https://github.com/strands-agents/samples) | Official samples. The `python/07-ux-demos/ag-ui-copilotkit-integration` example is our **canonical CopilotKit/AG-UI wiring**. |

All repos verified live as of May 2026 (sizes: 182, 743, 113, 1217 tracked files; default branch `main`).

---

## Repo 1 — `sample-fraud-investigation-assistance-using-aws-bedrock-strandsagents-mcp`

### 1.1 Backend file layout

Top-level:
```
app/
  action-group-schemas/         # Bedrock Agent OpenAPI specs (skip)
  containers/                    # MCP servers, one container per server
    brave_mcp/Dockerfile
    fetch_mcp/Dockerfile
    merchant_mcp/{handler.py, requirements.txt, tools_description.py, Dockerfile}
    transaction_mcp/{handler.py, requirements.txt, tools_description.py, Dockerfile}
  lambdas/                       # Lambda function code
    deploy-db/handler.py
    query-data/handler.py
    strands-agent-mcp/handler.py  # the Strands orchestrator agent
  layers/                        # Lambda layers (psycopg2, strands-agents)
build-script/                    # All shell glue for build/deploy
iac/{bootstrap,roots/app,templates/}   # Terraform IaC (skip — AWS-specific)
test/fut/                        # Functional tests (shell + Python)
ui/{app.py, Dockerfile, services/bedrock_agent_runtime.py}  # Streamlit UI
```

**Pattern:** one directory per deployable unit (MCP server, lambda, layer). Each has a single `handler.py` entrypoint, its own `requirements.txt`, and its own `Dockerfile` (for containers). No shared "src" layout.

### 1.2 Session persistence
**No app-level session store.** The whole UI relies on Bedrock Agent's managed conversation state via `bedrock_agent_runtime.invoke_agent(agentId, aliasId, sessionId, prompt)`. Streamlit holds a `sessionId` in `st.session_state` and that's it. **Skip entirely** — we need our own Postgres-backed store.

### 1.3 MCP wiring
Each MCP server is a **FastMCP** container (Anthropic FastMCP / Starlette / Uvicorn) on port 8080 at path `/mcp`, transport = **`streamable-http`** (stateless). Source: `app/containers/transaction_mcp/handler.py`. Tools are registered with `@mcp.tool()` decorators directly inside `handler.py`; tool descriptions are imported from a sibling `tools_description.py`. The container also serves `/healthz` and `/health`. CORS middleware allows all origins; proxy-headers middleware honors `X-Forwarded-*`.

The Strands client (in `app/lambdas/strands-agent-mcp/handler.py`) connects with one of two transports:
- Single endpoint: `MCPClient(lambda: streamablehttp_client(endpoint))`
- Multiple endpoints: `MCPClient(lambda: sse_client(endpoint))`, then aggregate `list_tools_sync()` across clients.

### 1.4 Frontend wiring
**Streamlit.** No SSE, no React, no AG-UI. The UI calls `bedrock_agent_runtime.invoke_agent()` synchronously and renders with `st.chat_message()` and `st.markdown(..., unsafe_allow_html=True)`. Citations and trace are rendered post-hoc from the response dict. **Skip.**

### 1.5 Hook patterns
None. The orchestrator is a Bedrock Agent (not Strands) — orchestration is done by Bedrock action groups. **Skip.**

### 1.6 Observability
None visible beyond CloudWatch logs and Bedrock's built-in trace events surfaced in the Streamlit UI. **Skip.**

### 1.7 Docker / compose
**Single Dockerfile per MCP server.** No `docker-compose.yml`. Local orchestration is provided by `Makefile` targets that drive Terraform. For our purposes the MCP-server Dockerfile shape is reusable.

### 1.8 Environment management
- `.env.TEMPLATE` files (placeholder syntax `###VAR###`) per container.
- `init.sh` is a wizard that takes user input and `sed`-substitutes `###${varName}###` across all template files. Substitutes into `set-env-vars.sh`, tfvars, params JSON, Makefile.
- Raw `os.environ`-style reads inside handlers. **No pydantic-settings.**

### 1.9 Tests
`test/fut/` ("functional unit tests"): a mix of bash drivers (`agent-tests.sh`, `mcp-client-tests.sh`) plus a Python invoker `call_bedrock_agent.py`. Test cases live in JSON (`agent-test-cases.json`, `mcp-test-cases.json`). No pytest. **Skip the framework**; the **idea** of declarative JSON test cases is interesting but we have better via `kloc-cli`-style contract tests.

### 1.10 Surprising / valuable
- **`tools_description.py` co-located next to `handler.py`** — clean separation of tool _spec text_ from tool _impl_. Worth lifting for our MCP tools.
- The **two-transport pattern** (`streamablehttp_client` single / `sse_client` multi) is a good piece of MCP know-how.
- **Knowledge base lives as markdown files in `data/knowledge-base/*.md`** — exactly the Anthropic Skills pattern, but glued via Bedrock KB rather than at agent runtime. We can mirror that file layout and load via Strands Skills.

### Repo 1: Copy / Adapt / Skip

| Verdict | Item | Path(s) |
|---|---|---|
| **Copy** | MCP server Dockerfile pattern | `app/containers/transaction_mcp/Dockerfile` |
| **Copy** | `tools_description.py` sibling convention | `app/containers/transaction_mcp/tools_description.py` |
| **Copy** | `streamable-http` MCP transport choice | `app/lambdas/strands-agent-mcp/handler.py` (the `streamablehttp_client` path) |
| **Adapt** | FastMCP server skeleton (port, `/mcp` path, health checks) | `app/containers/transaction_mcp/handler.py` — replace API Gateway httpx calls with our Postgres/Strands tool impls |
| **Adapt** | Knowledge-base-as-markdown convention | `data/knowledge-base/*.md` — but mount as Strands Skills directories, not Bedrock KB |
| **Skip** | All of `iac/`, `build-script/aws-quota-increase/`, `app/lambdas/`, Bedrock Agent action groups | Everything AWS-tied |
| **Skip** | Streamlit UI (`ui/`) | We have Next.js + CopilotKit |
| **Skip** | `test/fut/` bash drivers | Use pytest |
| **Skip** | `init.sh` wizard pattern | Use `.env` + `pydantic-settings` |

---

## Repo 2 — `sample-strands-agent-with-agentcore`

This repo has two completely different code regions:

- `agentcore/` (top-level) — **AgentCore-specific** sub-projects (`a2a-agents`, `gateway-tools`, `mcp-runtime`). Mostly skip.
- `chatbot-app/` — **The piece we want.** A FastAPI + Next.js production-shape app. The directory is named "agentcore" inside but the code is mostly portable Strands + FastAPI.

### 2.1 Backend file layout (`chatbot-app/agentcore/`)

```
chatbot-app/agentcore/
├── Dockerfile
├── pytest.ini
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── skills/                            # SKILL.md files per skill (Anthropic Skills layout)
│   ├── research-agent/SKILL.md
│   ├── tavily-search/SKILL.md
│   ├── web-search/SKILL.md
│   ├── arxiv-search/SKILL.md
│   ├── financial-news/SKILL.md
│   ├── gmail/SKILL.md
│   ├── github/SKILL.md
│   ├── notion/SKILL.md
│   └── ...
├── src/
│   ├── main.py                        # FastAPI app, 7 routers, lifespan
│   ├── agent/
│   │   ├── config/{constants.py, prompt_builder.py}
│   │   ├── factory/session_manager_factory.py   # picks Cloud (skip) vs Local (keep)
│   │   ├── gateway/mcp_client.py                # AgentCore Gateway client (mostly skip)
│   │   ├── hooks/
│   │   │   ├── email_approval.py
│   │   │   ├── github_approval.py
│   │   │   ├── research_approval.py
│   │   │   └── utils.py                         # resolve_tool_call (unwraps skill_executor)
│   │   ├── mcp/
│   │   │   ├── mcp_runtime_client.py            # MCP HTTP client w/ token + filtering
│   │   │   └── elicitation_bridge.py            # OAuth handoff via SSE queue
│   │   ├── processor/{file_processor.py, multimodal_builder.py}
│   │   ├── session/
│   │   │   ├── compacting_session_manager.py    # AgentCore-Memory backed (skip)
│   │   │   ├── local_session_buffer.py          # File-backed batching wrapper
│   │   │   └── unified_file_session_manager.py  # File-backed, cross-agent merge
│   │   ├── stop_signal.py
│   │   ├── tool_filter.py
│   │   └── voice_agent.py
│   ├── agents/
│   │   ├── base.py, chat_agent.py, factory.py, skill_chat_agent.py, workflow_agent.py
│   ├── builtin_tools/                 # Internal tools (code-interpreter, ppt, excel, etc.)
│   ├── local_tools/                   # File-system-aware tools
│   ├── models/{schemas.py, composer_schemas.py, swarm_schemas.py}
│   ├── registry/{client.py, loader.py}  # AgentCore Registry — mostly skip
│   ├── routers/
│   │   ├── chat.py                    # SSE stream chat
│   │   ├── health.py
│   │   ├── stop.py
│   │   ├── skills.py
│   │   ├── tools.py
│   │   ├── gateway_tools.py
│   │   ├── voice.py
│   │   └── browser_live_view.py
│   ├── skill/
│   │   ├── decorators.py              # @skill("name") attaches _skill_name to tools
│   │   ├── skill_registry.py          # scans skills/, reads SKILL.md frontmatter
│   │   └── skill_tools.py             # the skill_executor tool
│   ├── streaming/
│   │   ├── agui_event_formatter.py    # Strands events → AG-UI SSE events
│   │   ├── agui_event_processor.py    # The actual stream pump
│   │   ├── execution_registry.py      # SINGLETON: decouples execution from connection
│   │   └── skill_event_bus.py
│   ├── workflows/composer_workflow.py
│   └── workspace/{base_manager.py, config.py, managers.py}
└── tests/
    ├── conftest.py
    ├── fixtures/{mock_model_provider, mock_session_manager, mock_tools}.py
    ├── unit/ (~20 test files)
    └── integration/{e2e/, test_a2a_protocol.py, test_mcp_gateway.py, ...}
```

**Layout pattern:** `src/` as the package root + sub-packages by concern (`agents/`, `agent/` for primitives, `routers/`, `streaming/`, `skill/`, `models/`). Routers in one folder, included via `app.include_router()` in `main.py`. **`src/agent/` vs `src/agents/`** is a real (and confusing) split: `agents/` = concrete agent classes; `agent/` = subsystems (session, hooks, mcp, processor, factory). For `kloc-agent` we should pick **one** and stick with it (probably `agent/` only).

### 2.2 Session persistence

Two-mode factory in `src/agent/factory/session_manager_factory.py`:

- **Cloud mode** (skip): `CompactingSessionManager` backed by AgentCore Memory; uses `MEMORY_ID` env var.
- **Local mode** (keep): `UnifiedFileSessionManager` (extends Strands' `FileSessionManager`) — reads/writes JSON files under:
  ```
  session_<id>/
    agents/
      agent_default/messages/message_*.json
      agent_voice/messages/message_*.json
  ```
- `LocalSessionBuffer` is a **wrapper** that buffers N messages before flushing to disk (default 5). Smart for local dev. We'll replace the file backend with Postgres but the **batching wrapper** idea is good.

### 2.3 MCP wiring

`src/agent/mcp/mcp_runtime_client.py` — establishes an HTTP-streaming MCP client at runtime (per-request). Auth is JWT Bearer. Endpoint discovery via AgentCore Registry (skip — we'll hardcode/env). Tools are wrapped in `FilteredMCPClient` which whitelists by prefix (default `mcp_`).

`src/agent/mcp/elicitation_bridge.py` — **gem.** When an MCP tool needs OAuth (e.g. Gmail), it calls `ctx.elicit_url(url)`. The bridge:
1. Pushes an `oauth_elicitation` event onto the outbound SSE queue (frontend sees it, opens a popup).
2. Blocks on a completion store (DDB cloud / in-memory dict local) until the frontend posts back.
3. Calls token-finalize, returns control to the MCP tool.

This is **exactly** the human-in-the-loop pattern we want for any approval/credential gates.

### 2.4 Frontend wiring

`chatbot-app/frontend/` — **Next.js 14 + React 18 + Tailwind**. Crucially: **NO CopilotKit.** Direct **AG-UI client** integration with `@ag-ui/client@^0.0.45` and `@ag-ui/core@^0.0.45`.

Hook architecture:
- `src/hooks/useChat.ts` — top-level chat hook; orchestrates `useChatAPI`, `useStreamEvents`, `useChatSessions`, `usePolling`, etc.
- `src/hooks/useChatAPI.ts` — opens SSE via `fetch()` + `ReadableStream` reader (not `EventSource`, so it can POST + send Authorization headers).
- `src/utils/sseParser.ts` — pure parser: `parseSSELine`, `parseSSEChunk`, `validateAGUIStreamEvent`, `serializeToSSE`. Type-aware: distinguishes RUN_STARTED, TEXT_MESSAGE_CONTENT, TOOL_CALL_*, etc.
- `src/app/api/stream/chat/route.ts` — Next.js route as BFF proxy. Resizes images, injects user context, forwards SSE.

So this repo demonstrates **AG-UI without CopilotKit**, which is option A for `kloc-agent`. Compare with repo 4 below for option B (CopilotKit on top).

### 2.5 Hook patterns

Strands `HookProvider` subclasses in `src/agent/hooks/`. All **in-process** (no HTTP webhooks). Pattern:

```python
class ResearchApproval(HookProvider):
    def register_hooks(self, registry):
        registry.add_callback(BeforeToolCallEvent, self.before_tool_call)
    async def before_tool_call(self, event):
        tool_name, tool_input = resolve_tool_call(event)  # unwraps skill_executor
        if tool_name == "research_agent" and self.needs_approval(tool_input):
            # event.interrupt() pauses agent, prompts user, returns a value
            decision = await event.interrupt(...)
            if decision == "deny":
                event.cancel_tool = True
```

The `resolve_tool_call` helper (in `hooks/utils.py`) is **critical** — it unwraps `skill_executor` wrapper calls so the hook sees the real underlying tool name. We need an identical helper.

### 2.6 Observability

`requirements.txt` includes `strands-agents[a2a,otel]>=1.30.0` and explicit OpenTelemetry packages. The Dockerfile starts uvicorn with **`opentelemetry-instrument`**:

```
CMD ["opentelemetry-instrument", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Zero-config OTel via auto-instrumentation. Exporter is configured by environment variables (`OTEL_EXPORTER_OTLP_ENDPOINT`, etc.) so it's trivially swappable to Tempo / Honeycomb / Langfuse via OTLP.

### 2.7 Docker / compose

- `chatbot-app/agentcore/Dockerfile` — Python 3.13-slim base, installs LibreOffice + poppler (skip: those are for the Office Skills builtin), `requirements.txt`, then `nova-act` separately with `--no-deps`. Health check on `/health`. Runs OTel-instrumented uvicorn on `:8080`.
- `chatbot-app/frontend/Dockerfile` + `Dockerfile.simple` — Next.js prod build.
- **No `docker-compose.yml`.** Local startup uses `setup.sh` (creates venv, `pip install`, `npm install`) and `start.sh` (clears ports 8080/3000, loads `chatbot-app/.env`, fetches secrets from AWS Secrets Manager if available, runs both backend and frontend in background, traps Ctrl+C).

For `kloc-agent` we'll prefer a real `docker-compose.yml` (backend, frontend, postgres, minio, optional MCP servers).

### 2.8 Environment management

- `.env` files at `chatbot-app/.env` are the source of truth for local dev.
- `start.sh` loads them with `set -a; source chatbot-app/.env; set +a`.
- Backend code reads via raw `os.environ.get("MEMORY_ID")`, `os.environ.get("DYNAMODB_USERS_TABLE")`. **No pydantic-settings.** Each module makes its own decisions about defaults.

This is workable but messy; we should use `pydantic-settings` with a single `settings.py`.

### 2.9 Tests

`pytest.ini`:
```
testpaths = tests
asyncio_mode = auto
addopts = -v --tb=short -m "not e2e"
markers = unit, integration, e2e
```

E2E tests opt-in via `-m e2e`. Unit tests use fixtures in `tests/fixtures/` (mock model provider, mock session manager, mock tools). Integration tests cover MCP gateway, A2A protocol, tool/agent contracts, workspace. E2E tests live in `tests/integration/e2e/` and hit a real backend over SSE (`sse_client.py`, Cognito helper). **Excellent shape**, lift it wholesale.

### 2.10 Surprising / valuable

- **`src/streaming/execution_registry.py`** — a singleton that maps `execution_id → Execution(events: list[SSEEvent], status, ts)`. Lets the agent run as `asyncio.create_task(...)`, append events to the buffer, and **let clients reconnect** by tailing from a cursor. Buffer caps at 10k events; completed executions evict 5 min after finish. This is the **decoupled SSE pattern** described in `03-runner-mgmt.md`. Direct lift.
- **`src/agent/stop_signal.py`** — abstract `StopSignalProvider` with `DynamoDBStopSignalProvider`. The router `routers/stop.py` writes a stop flag; the agent's run-loop polls it and calls `agent.cancel()`. For `kloc-agent`, the same shape with a Postgres backend (or even an in-process `dict[session_id, threading.Event]`) is clean.
- **`src/skill/`** — implements **Anthropic-Skills-style** progressive disclosure on top of Strands. Three layers:
  - L1: `<available_skills>` summary in system prompt (name + description from `SKILL.md` frontmatter)
  - L2: `SKILL.md` body loaded on demand via the `skills` built-in tool
  - L3: actual tool functions, tagged with `@skill("name")` decorator that sets `_skill_name` attr
- **`src/streaming/agui_event_formatter.py`** — the canonical Strands→AG-UI mapping table (covered in `01-strands-minimal.md`). Lift the file.
- **`src/agent/hooks/utils.py:resolve_tool_call`** — 7 lines, but essential glue for any agent that routes everything through a `skill_executor` (which we'll do).
- **`src/agent/mcp/elicitation_bridge.py`** — the OAuth / human-in-the-loop pattern, transport-agnostic via the SSE outbound queue.
- **No global agent singleton.** `agents/factory.py:create_agent(request_type, session_id, user_id, ...)` returns a **fresh agent per request**, isolated by session_id. This is what we want — per-session ephemeral runners.

### Repo 2: Copy / Adapt / Skip

| Verdict | Item | Path |
|---|---|---|
| **Copy** | `streaming/execution_registry.py` shape | `chatbot-app/agentcore/src/streaming/execution_registry.py` |
| **Copy** | `streaming/agui_event_formatter.py` | `chatbot-app/agentcore/src/streaming/agui_event_formatter.py` |
| **Copy** | `routers/health.py` and `routers/stop.py` | `chatbot-app/agentcore/src/routers/{health,stop}.py` |
| **Copy** | `skill/decorators.py`, `skill/skill_registry.py`, `skill/skill_tools.py` | `chatbot-app/agentcore/src/skill/` |
| **Copy** | `agent/hooks/utils.py:resolve_tool_call` | `chatbot-app/agentcore/src/agent/hooks/utils.py` |
| **Copy** | Hook class pattern (HookProvider + add_callback + interrupt) | `chatbot-app/agentcore/src/agent/hooks/research_approval.py` |
| **Copy** | `pytest.ini` + tests/unit + tests/integration layout | `chatbot-app/agentcore/pytest.ini` + `tests/` |
| **Copy** | `tests/fixtures/mock_*.py` shape | `chatbot-app/agentcore/tests/fixtures/` |
| **Copy** | E2E SSE client helper | `chatbot-app/agentcore/tests/integration/e2e/sse_client.py` |
| **Copy** | `agents/factory.py:create_agent()` per-session pattern | `chatbot-app/agentcore/src/agents/factory.py` |
| **Copy** | `Dockerfile` `opentelemetry-instrument` startup | `chatbot-app/agentcore/Dockerfile` |
| **Copy** | `frontend/src/utils/sseParser.ts` | `chatbot-app/frontend/src/utils/sseParser.ts` |
| **Copy** | `frontend/src/hooks/useChat.ts` + `useChatAPI.ts` + `useStreamEvents.ts` shape | `chatbot-app/frontend/src/hooks/` |
| **Copy** | `frontend/src/app/api/stream/chat/route.ts` BFF proxy shape | `chatbot-app/frontend/src/app/api/stream/chat/route.ts` |
| **Copy** | Skills directory layout (`skills/<name>/SKILL.md`) | `chatbot-app/agentcore/skills/` |
| **Adapt** | `agent/factory/session_manager_factory.py` | Drop cloud-mode branch; replace `UnifiedFileSessionManager` with a Postgres-backed Strands session manager |
| **Adapt** | `agent/session/local_session_buffer.py` batching wrapper | Wrap our Postgres session mgr instead of FileSessionManager |
| **Adapt** | `agent/mcp/mcp_runtime_client.py` | Replace AgentCore Registry endpoint discovery with `MCP_SERVERS` env var / config file |
| **Adapt** | `agent/mcp/elicitation_bridge.py` | Drop DynamoDB cloud branch; keep in-memory dict + lock |
| **Adapt** | `agent/stop_signal.py` | Replace DynamoDB provider with Postgres / in-process provider |
| **Adapt** | `routers/chat.py` SSE pattern | Drop agentcore-specific state fields; keep the buffer-tail + keepalive + reconnection shape |
| **Adapt** | `main.py` FastAPI bootstrap | Drop AgentCore env handling; add `pydantic-settings` |
| **Adapt** | `agents/chat_agent.py` | Drop AgentCore Memory; keep CacheConfig, retry config, hook attachment, tool-source merging |
| **Skip** | `agent/gateway/mcp_client.py` (AgentCore Gateway client) | AgentCore-only |
| **Skip** | `agent/session/compacting_session_manager.py` | AgentCore Memory-only |
| **Skip** | `registry/` (AgentCore Registry) | Replace with simple YAML/JSON config |
| **Skip** | `routers/gateway_tools.py`, `routers/browser_live_view.py`, `routers/voice.py` | Out of scope features |
| **Skip** | `agentcore/` (sibling to chatbot-app: a2a-agents/, gateway-tools/, mcp-runtime/) | AgentCore deployment artifacts |
| **Skip** | `cowork/`, `mobile-app/`, `telegram-app/`, `infra/` | Not relevant |
| **Skip** | `builtin_tools/` (office, dcv-sdk, nova-act, browser) | Outside research-agent scope |
| **Skip** | Cognito / Amplify / DynamoDB-client frontend code | Use our own auth |

---

## Repo 3 — `sample-AIOPS-agent-bedrock-strandsagents`

### 3.1 Backend file layout

```
src/
├── __init__.py
├── main.py                        # FastAPI app, CORS=*, 11 routers, no lifespan
├── agents/
│   ├── alarm_analyzer.py
│   ├── billing_analyzer.py
│   ├── case_analyzer.py
│   ├── correlation_engine.py
│   ├── health_agent.py
│   └── investigation_agent.py     # the main multi-tool agent
├── api/
│   ├── routes.py                  # core /regions, /vpcs, /topology endpoints
│   ├── alarm_routes.py
│   ├── auth_routes.py
│   ├── billing_routes.py
│   ├── case_routes.py
│   ├── config_routes.py
│   ├── dashboard_routes.py
│   ├── export_routes.py
│   ├── health_routes.py
│   ├── investigation_routes.py    # /chat, /chat/stream (SSE), /chat/clear, /chat/history
│   └── workspace_routes.py
├── collectors/                    # Domain data collectors (AWS API readers)
├── models/                        # Pydantic models
├── services/                      # Persistence + domain logic
│   └── dynamodb_service.py        # All session/workspace persistence
└── visualizer/
tests/
└── test_*.py                      # pytest at top level
scripts/
├── create_tables.py               # DynamoDB schema setup
└── generate_architecture_diagram.py
frontend/                          # Vue 3 + Vite + Element Plus
└── src/{api,auth,components,i18n}
```

**Pattern:** "one file per route" at `src/api/*_routes.py`, "one file per agent" at `src/agents/*.py`, "one service per persistence concern" at `src/services/*.py`. `routes.py` (no suffix) is the "core" router; everything else uses `<domain>_routes.py`.

### 3.2 Session persistence

**DynamoDB only**, all in `src/services/dynamodb_service.py`. Two tables:
- `vpc-topology-workspaces` (Pk: `id`)
- `vpc-topology-snapshots` (Pk: `workspace_id`, Sk: `snapshot_id`, GSI on `vpc_id`)

Pattern is simple: pydantic model → `.model_dump_json()` → DDB item; reverse on read. **Replace** with our Postgres + SQLAlchemy approach. `scripts/create_tables.py` is a small Boto3 script — direct equivalent is our Alembic migrations.

The `investigation_agent.py` keeps **in-process per-session conversation history** in a Python dict keyed by session_id (no DB persistence). Resets via `/chat/clear`. Fine for dev; we need DB-backed for kloc-agent.

### 3.3 MCP wiring

**None.** All tools are local Python functions decorated with `@tool` calling Boto3. **Skip.**

### 3.4 Frontend wiring

**Vue 3 + Vite + Element Plus + Vue Flow.** Not Next.js / not React. Per-domain API client modules (`src/api/alarm.js`, `src/api/billing.js`, ...) that wrap fetch calls. SSE consumed in `src/api/investigation.js` for `/chat/stream`. **Skip the framework choice**; the **modular API client per domain** is a sensible pattern.

### 3.5 Hook patterns

None. **Skip.**

### 3.6 Observability

Basic Python logging only. No OTel, no Langfuse. **Skip.**

### 3.7 Docker / compose

**No Dockerfile, no compose.** README expects `pip install` + `uvicorn` for backend and `npm run dev` for frontend. **Skip.**

### 3.8 Environment management

- `.env.example` with sections per LLM provider (`bedrock` / `ollama` / `siliconflow`) and AWS credentials.
- `python-dotenv` `load_dotenv()` at the top of `main.py`.
- Direct `os.environ` reads scattered across modules. **Skip the pattern**, use pydantic-settings.

The `.env.example` does demonstrate a useful **multi-provider switch** pattern: a single `LLM_PROVIDER=bedrock|ollama|siliconflow` env var that the model factory routes on. We're already multi-provider (Anthropic/OpenRouter/Bedrock) so this is good confirmation.

### 3.9 Tests

`tests/test_*.py` flat at repo root. Files: `test_correlation_engine.py`, `test_dashboard_aggregator.py`, `test_dashboard_routes.py`, `test_issue_state_service.py`. pytest, no special config visible. Not impressive — repo 2's test layout is strictly better.

### 3.10 Surprising / valuable

- **File-per-route + file-per-agent layout** at `src/api/` and `src/agents/`. Clean, easy to navigate, easy to grep. This is the **simplest backend skeleton** of the 4 repos and worth using as a starter shape for kloc-agent (then layer repo 2's session/streaming patterns on top).
- **40+ `@tool` methods on a single class** (`investigation_agent.py`). Shows that the "agent class with many `@tool` methods" pattern scales, but it's noisy. We'll prefer one `tools/` module per concern.
- **`investigate_stream()` as async generator + FastAPI StreamingResponse** is a clean minimal SSE pattern, no execution registry — but it has no resilience (client disconnect drops the run). Repo 2's pattern is strictly better.

### Repo 3: Copy / Adapt / Skip

| Verdict | Item | Path |
|---|---|---|
| **Copy** | `src/api/<domain>_routes.py` naming convention | `src/api/investigation_routes.py` etc. |
| **Copy** | `src/agents/<concern>.py` one-file-per-agent layout | `src/agents/investigation_agent.py` etc. |
| **Copy** | Multi-provider model factory env switch (`LLM_PROVIDER=...`) | `.env.example` + `create_model_from_config()` in `investigation_agent.py` |
| **Adapt** | `src/main.py` FastAPI bootstrap | Add lifespan, replace CORS=* with explicit origins, add pydantic-settings |
| **Adapt** | `/chat/stream` async-generator SSE | Use **only as a fallback**; the execution-registry pattern from repo 2 is preferred |
| **Skip** | `dynamodb_service.py` | Use Postgres |
| **Skip** | Frontend (Vue) | We use Next.js |
| **Skip** | All AWS-specific collectors (`vpc_collector.py`, etc.) | Not our domain |
| **Skip** | `scripts/create_tables.py` | Use Alembic |

---

## Repo 4 — `strands-agents/samples`

This is a samples library, not one project. The relevant subtrees:

### 4.1 `python/07-ux-demos/ag-ui-copilotkit-integration/` — **The canonical CopilotKit + AG-UI wiring**

```
ag-ui-copilotkit-integration/
├── README.md
├── start.sh                          # Launches both services with trap cleanup
├── agent/
│   ├── pyproject.toml                # uv-managed
│   ├── uv.lock
│   ├── main.py                       # FastAPI + StrandsAgent + AG-UI on :8001
│   ├── tools.py                      # @tool functions (search, checklist, etc.)
│   └── knowledge/                    # Markdown KB
└── frontend/
    ├── package.json                  # @copilotkit/react-{core,ui,runtime}@1.50.0 + @ag-ui/client@0.0.42
    ├── next.config.mjs
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx              # <CopilotKit runtimeUrl="/api/copilotkit" agent="strands_agent">
        │   ├── api/
        │   │   ├── copilotkit/route.ts   # CopilotRuntime + HttpAgent → agent-proxy
        │   │   └── agent-proxy/route.ts  # Receives copilot calls, builds AG-UI RunAgentInput, fetches AGENT_URL
        │   └── globals.css
        ├── components/{quiz-card.tsx, source-card.tsx}
        └── lib/utils.ts
```

**Stack:** FastAPI 8001 (Python) + Next.js 14 3001 (TypeScript) + Strands. Backend wraps Strands `Agent` in `StrandsAgent`/`StrandsAgentConfig` (an AG-UI helper) that exposes a single POST endpoint receiving AG-UI `RunAgentInput`.

**CopilotKit wiring** — three-layer:
1. `frontend/src/app/page.tsx`: `<CopilotKit runtimeUrl="/api/copilotkit" agent="strands_agent">{children}</CopilotKit>` — CopilotKit provider.
2. `frontend/src/app/api/copilotkit/route.ts`: Next.js route handler using `copilotRuntimeNextJSAppRouterEndpoint` with `CopilotRuntime` containing an `HttpAgent({ url: "http://localhost:3001/api/agent-proxy" })`. **Empty adapter** (`new EmptyAdapter()`-equivalent) because the agent itself is doing the LLM work.
3. `frontend/src/app/api/agent-proxy/route.ts`: receives CopilotKit's call, ensures every message has an id (UUIDs via Node crypto), builds AG-UI-compliant body (`{threadId, runId, messages, tools, context, state, forwardedProps}`), `fetch()`s `AGENT_URL` (env), proxies the SSE stream back. GET handler returns the agent's metadata (id `"strands_agent"`) so CopilotKit can discover it.

So the **chain** is: browser → CopilotKit React → `/api/copilotkit` → CopilotRuntime → `/api/agent-proxy` → Python agent on `:8001`. The proxy is purely to insert the AG-UI envelope; CopilotRuntime alone doesn't know how.

**Shared state pattern** — `useCoAgent<ChecklistState>()` from CopilotKit + `STATE_SNAPSHOT` events from AG-UI keep frontend state and agent state in sync. Tools that mutate state return JSON which the AG-UI helper extracts and emits as `STATE_SNAPSHOT`.

**Frontend tools** — `show_quiz_question`, `show_notification` are registered on the frontend via `useCopilotAction(...)`; the agent only "mentions" them in the prompt — execution is browser-side.

### 4.2 `python/01-learn/16-hooks-lifecycle/`

Notebook-based tutorial. Confirms hook types and registration:
- Events: `AgentInitializedEvent`, `BeforeInvocationEvent` / `AfterInvocationEvent`, `BeforeModelCallEvent` / `AfterModelCallEvent`, `BeforeToolCallEvent` / `AfterToolCallEvent`, `MessageAddedEvent`.
- Registration: `agent.add_hook(MyHook())`; subclass `HookProvider`; implement `register_hooks(self, registry)` calling `registry.add_callback(EventType, fn)`.
- Callbacks may be sync or async; Strands awaits async automatically.
- Writable fields on events: `messages`, `cancel_tool`, `retry`, `resume`.
- **All in-process.** No HTTP webhooks anywhere.

### 4.3 `python/01-learn/15-skills/`

Confirms Strands' built-in `AgentSkills` plugin scans a directory for `SKILL.md` files. Frontmatter:
```yaml
---
name: returns-policy
description: Customer returns and refunds policy
---
```
Plugin injects `<available_skills>` summary into system prompt + provides a `skills` tool that loads the full body on demand. `set_available_skills()` allows runtime modification. Cleanly matches the Anthropic Skills convention; repo 2 builds on top with tool-binding and progressive disclosure.

### 4.4 `python/05-technical-use-cases/rag/agentic-rag/adaptive-structured-rag/`

CLI-shaped Strands example: `main.py` parses argv (`--question`, `--engine`), `src/agent.py` constructs the agent, `src/tools/{athena_tool,knowledge_base_tool,sqllite_tool}.py` provide tools. **Not a web service.** Useful as a confirmation of `src/agent.py` + `src/tools/*.py` shape, but no infra patterns beyond that.

### 4.5 `python/04-industry-use-cases/.../core/session_state.py`

Just one Python file showing per-session state held in a dict, persisted via boto3 to DynamoDB. Not worth lifting.

### 4.6 Tests

The samples repo's tests are mostly inside notebooks. Skip.

### 4.7 Docker / compose / observability / env

Each sample is independent; most have a `start.sh` + Python venv + `npm install`. Some use `uv` (`pyproject.toml` + `uv.lock`) — the AG-UI demo uses uv. Worth following for our backend (we already prefer uv per memory).

### 4.8 Surprising / valuable

- **`StrandsAgent`/`StrandsAgentConfig` (from `ag-ui-protocol` Python package) is a real wrapper** that converts a Strands `Agent` into an AG-UI HTTP endpoint. It handles `RunAgentInput` parsing, SSE emission, and state-extraction callbacks. We must verify the package name/API (`ag-ui-protocol>=0.1.13` per repo 2's requirements.txt) and either use it or hand-roll an equivalent.
- The **agent-proxy Next.js route** is the missing piece in CopilotKit + AG-UI docs — it's what makes everything work locally because `CopilotRuntime`'s `HttpAgent` doesn't natively speak AG-UI's `RunAgentInput` envelope.
- **CopilotKit doesn't replace AG-UI; it sits on top.** You still need an AG-UI-speaking backend. The choice is "CopilotKit + AG-UI" (repo 4 — gives you `useCoAgent`, `useCopilotAction`, frontend tools, ready-made chat UI) vs "raw AG-UI client" (repo 2 — full control, more code).

### Repo 4: Copy / Adapt / Skip

| Verdict | Item | Path |
|---|---|---|
| **Copy** | `agent/main.py` (FastAPI + `StrandsAgent` + AG-UI wrapper) | `python/07-ux-demos/ag-ui-copilotkit-integration/agent/main.py` |
| **Copy** | `frontend/src/app/api/copilotkit/route.ts` (CopilotRuntime + HttpAgent → proxy) | `python/07-ux-demos/ag-ui-copilotkit-integration/frontend/src/app/api/copilotkit/route.ts` |
| **Copy** | `frontend/src/app/api/agent-proxy/route.ts` (AG-UI envelope builder + SSE forwarder) | `python/07-ux-demos/ag-ui-copilotkit-integration/frontend/src/app/api/agent-proxy/route.ts` |
| **Copy** | `frontend/src/app/page.tsx` `<CopilotKit>` provider shape | `python/07-ux-demos/ag-ui-copilotkit-integration/frontend/src/app/page.tsx` |
| **Copy** | `start.sh` two-service launcher + trap cleanup | `python/07-ux-demos/ag-ui-copilotkit-integration/start.sh` |
| **Copy** | `agent/pyproject.toml` + uv lockfile shape | `python/07-ux-demos/ag-ui-copilotkit-integration/agent/pyproject.toml` |
| **Copy** | Hook patterns from `01-learn/16-hooks-lifecycle/` | Notebooks — adapt code to our `HookProvider` subclasses |
| **Copy** | Skills directory pattern from `01-learn/15-skills/` | `python/01-learn/15-skills/skills/{name}/SKILL.md` |
| **Adapt** | `useCoAgent` shared-state pattern | Use for kloc-agent's research artifact state |
| **Adapt** | `useCopilotAction` frontend-tool pattern | Use for kloc-agent's "open artifact in side panel" etc. |
| **Skip** | All `02-deploy/04-agentcore-multi-agent/` | AgentCore |
| **Skip** | AWS-specific Bedrock model config defaults in demos | Use our model factory |

---

## Cross-repo synthesis

### Common conventions across the 4 repos

1. **FastAPI everywhere** (repos 1's UI is the outlier with Streamlit; the orchestrator in repo 1 is a Bedrock Agent so doesn't apply). FastAPI + uvicorn is the unanimous backend choice for Strands web apps.
2. **`src/` package root.** Repos 2, 3, 4-AG-UI-demo all use `src/<package>/` layout. We'll follow.
3. **File-per-route in `src/api/` or `src/routers/`** (repos 2 & 3). One file per domain. Routes registered in `main.py` via `app.include_router()`.
4. **File-per-agent in `src/agents/`** (repos 2 & 3). One file per agent class. Repo 4's AG-UI demo has a single agent, so it's just `agent/main.py` — but the pattern scales as a directory.
5. **Tools as `@tool`-decorated functions**, either in `tools.py` (repo 4), `tools/<domain>.py` (repo 2's `local_tools/`, `builtin_tools/`), or as methods on an agent class (repo 3 — not recommended at scale).
6. **MCP via streamable-HTTP transport**, JWT bearer auth, filtered by name prefix (repos 1 & 2 agree).
7. **Sessions as JSON files** for local dev (`session_<id>/agents/<agent_id>/messages/message_*.json`, repo 2) or DDB for cloud (repos 2 & 3). The Strands `FileSessionManager` base class is the lingua franca.
8. **Per-session ephemeral agents.** Repo 2's `agents/factory.py:create_agent(session_id, user_id)` returns a fresh instance every call; no global singletons. Repo 3 keeps a dict but that's an outlier.
9. **Hooks are in-process Python classes**, not HTTP webhooks. All 4 repos agree (repo 4's notebook is explicit, repo 2's implementations confirm). `HookProvider.register_hooks(registry) → registry.add_callback(EventType, fn)`.
10. **SSE for streaming**, not WebSocket. All web-facing samples (repos 2, 3, 4-AG-UI) use `text/event-stream` with `Content-Type: text/event-stream` + `Cache-Control: no-cache`.
11. **`.env` files + raw `os.environ`** is the dominant env pattern. None of the 4 repos use `pydantic-settings`. We'll diverge here — pydantic-settings is strictly better.
12. **Skills directory layout** (`skills/<name>/SKILL.md` + optional `references/*.md`) is now standard and tracks Anthropic's convention.

### Disagreements & which side to pick

| Concern | Repo 2 (chatbot-app) | Repo 3 (AIOPS) | Repo 4 (AG-UI demo) | **Our pick** |
|---|---|---|---|---|
| Frontend protocol | Raw AG-UI client | Custom Vue + fetch | CopilotKit + AG-UI | **CopilotKit + AG-UI** (repo 4) — `useCoAgent` + `useCopilotAction` save a lot of code; but lift sseParser + useChat-style hooks from repo 2 as a fallback for non-Copilot pages |
| SSE resilience | Background task + ExecutionRegistry + cursor replay | Async generator (no replay) | Async generator (no replay) | **Execution registry pattern from repo 2.** Required for our long-running research runs. |
| Session backend | File JSON OR AgentCore Memory | DynamoDB | (none, single-shot demos) | **Postgres** via custom Strands `SessionManager` subclass; wrap with `LocalSessionBuffer`-style batcher (repo 2) |
| Stop signal | DDB-backed singleton + agent.cancel() | Not implemented | Not implemented | **In-process `dict[session_id, asyncio.Event]` + cancel()** (repo 2's pattern, simpler backend) |
| Test layout | `tests/{unit,integration,e2e}` + pytest.ini markers + fixtures dir | Flat `tests/test_*.py` | Notebooks only | **Repo 2's layout, wholesale.** |
| Observability | OTel via `opentelemetry-instrument` in Dockerfile CMD | None | None | **OTel auto-instrumentation** (repo 2). Optional Langfuse exporter via OTLP. |
| Env mgmt | `.env` + `os.environ` | `.env` + `python-dotenv` + `os.environ` | uv + `.env.example` | **`pydantic-settings`** — diverges from all 4 but is what we want for type-safety + central `Settings` class. |
| Docker | Single Dockerfile per service, no compose | None | None | **`docker-compose.yml`** (backend, frontend, postgres, minio); single Dockerfile per service modeled on repo 2's. |
| MCP server framework | FastMCP (Anthropic's) | (none) | (none) | **FastMCP** — repo 1's pattern. |
| Multi-provider models | (Bedrock only) | env switch `LLM_PROVIDER=bedrock\|ollama\|siliconflow` | (Bedrock only) | **Repo 3's env-switch pattern**, plus Anthropic + OpenRouter providers. |
| Hook scope | All in-process, no async webhooks | (none) | All in-process | **In-process only** for now. HTTP webhooks would be a future feature. |
| Per-session agents | Fresh instance per call (factory.py) | In-process dict cache | Fresh per call | **Fresh per call** — already aligns with our "per-session ephemeral runner" goal. |

### Specific files we'll start by copying into `kloc-agent`

Initial drop, in priority order:

1. **`src/streaming/execution_registry.py`** ← repo 2 (the heart of decoupled runs)
2. **`src/streaming/agui_event_formatter.py`** ← repo 2 (Strands → AG-UI)
3. **`src/routers/{health,stop}.py`** ← repo 2 (trivial, lifts cleanly)
4. **`src/agent/hooks/utils.py`** ← repo 2 (the `resolve_tool_call` helper)
5. **`src/agent/hooks/research_approval.py`** ← repo 2 (as a template for our own approval hook)
6. **`src/skill/{decorators,skill_registry,skill_tools}.py`** ← repo 2 (Anthropic Skills loader on top of Strands)
7. **`frontend/src/utils/sseParser.ts`** ← repo 2 (AG-UI SSE parser)
8. **`frontend/src/app/api/copilotkit/route.ts`** ← repo 4 (CopilotRuntime config)
9. **`frontend/src/app/api/agent-proxy/route.ts`** ← repo 4 (AG-UI envelope builder, the missing glue piece)
10. **`frontend/src/app/page.tsx`** ← repo 4 (`<CopilotKit>` provider + `useCoAgent`)
11. **`agent/main.py`** ← repo 4 (FastAPI + AG-UI `StrandsAgent` wrapper)
12. **`tests/{unit,integration,e2e}/`** ← repo 2 (full layout + pytest.ini + fixtures)
13. **`Dockerfile`** ← repo 2 (with `opentelemetry-instrument` startup)
14. **MCP server Dockerfile + `handler.py` + `tools_description.py`** ← repo 1 (FastMCP container shape)

### Hybrid layout we'll end up with

```
kloc-agent/
├── docker-compose.yml                  # NEW
├── pyproject.toml                      # uv-managed (repo 4 style)
├── .env.example                        # repo 3 style multi-provider
├── Dockerfile                          # repo 2 OTel pattern
├── alembic/                            # NEW — replaces repo 3's create_tables.py
├── src/
│   ├── main.py                         # FastAPI + lifespan, pydantic-settings, repo 2 router registration
│   ├── settings.py                     # NEW — pydantic-settings (no repo precedent)
│   ├── agent/
│   │   ├── factory.py                  # per-session, from repo 2's agents/factory.py
│   │   ├── chat_agent.py               # from repo 2's agents/chat_agent.py
│   │   ├── hooks/                      # from repo 2's agent/hooks/
│   │   ├── mcp/                        # from repo 2's agent/mcp/ (minus AgentCore)
│   │   ├── session/                    # NEW — Postgres session manager + LocalSessionBuffer-style wrapper
│   │   └── stop_signal.py              # adapted from repo 2 (in-proc backend)
│   ├── api/                            # repo 3's file-per-route names + repo 2's chat-router shape
│   │   ├── chat.py
│   │   ├── health.py
│   │   ├── stop.py
│   │   ├── sessions.py
│   │   └── skills.py
│   ├── skill/                          # repo 2's skill/
│   ├── skills/                         # SKILL.md directories — repo 2 + repo 4 convention
│   ├── streaming/                      # repo 2's streaming/
│   ├── tools/                          # NEW — domain tools, file-per-concern
│   └── models/                         # pydantic schemas
├── tests/
│   ├── unit/, integration/, e2e/       # repo 2 layout
│   ├── conftest.py + fixtures/
│   └── pytest.ini                      # repo 2 verbatim
├── mcp-servers/                        # repo 1 pattern — one dir per MCP server
│   └── kloc/{handler.py, tools_description.py, Dockerfile, requirements.txt}
└── frontend/                           # Next.js 14 + CopilotKit + AG-UI
    ├── package.json                    # repo 4 versions: @copilotkit/* 1.50.0 + @ag-ui/client
    ├── Dockerfile
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx                # repo 4 <CopilotKit> + useCoAgent
        │   └── api/
        │       ├── copilotkit/route.ts # repo 4
        │       └── agent-proxy/route.ts# repo 4
        ├── components/
        ├── hooks/                      # repo 2's useChat/useChatAPI/useStreamEvents (for advanced pages)
        └── utils/sseParser.ts          # repo 2 verbatim
```

### Two things to investigate further

1. **`ag-ui-protocol` Python package's `StrandsAgent`/`StrandsAgentConfig` API** — repo 4 uses it as the AG-UI server wrapper; repo 2 hand-rolls equivalent code in `streaming/agui_event_processor.py`. Decide: use the library or hand-roll. Library is cleaner if `useCoAgent` state-extraction hooks are first-class; hand-rolled gives us repo 2's execution-registry resilience.
2. **CopilotRuntime + HttpAgent + agent-proxy** vs **raw `@ag-ui/client` calls from `useChat`** — repo 4 needs the proxy because CopilotRuntime is opinionated; repo 2 skips CopilotKit entirely and gets a leaner stack. If we want `useCoAgent` / `useCopilotAction` we accept the proxy. If we don't, we can skip CopilotKit (and 3 npm packages) and emit AG-UI directly. Likely answer: **adopt CopilotKit** because the productivity wins on bidirectional state + frontend tools are real, and the proxy is 50 lines of TS.
