# kloc-agent — Investigation Synthesis

> First investigation pass. Builds on `poc.md` and consumes the five per-stream
> briefs under `docs/research/`. End-state: enough decisions locked to write a
> tight implementation plan via the `writing-plans` skill.
>
> **Date:** 2026-05-14. **Status:** decisions locked except where explicitly
> flagged as "defer." Verified package versions: `strands-agents` **1.39.0**,
> `ag-ui-protocol` **0.1.18**, `ag_ui_strands` **0.1.8**, `@copilotkit/runtime`
> **1.52.1**, `@ag-ui/client` **^0.0.42**, `strands_agentskills` **0.2.0**
> (git-only), Next.js **16.0.8**, Python **3.12** (runners + backend).

---

## 0. Per-stream briefs (read order)

| # | Topic | File |
|---|------|------|
| 01 | Strands minimal-viable usage | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) |
| 02 | Backend + AG-UI streaming (critical path) | [`research/02-backend-agui.md`](research/02-backend-agui.md) |
| 03 | Runner management & isolation | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) |
| 04 | Persistence (Postgres) + storage (MinIO) | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) |
| 05 | Reference projects — infra extraction | [`research/05-reference-projects.md`](research/05-reference-projects.md) |

When this doc says "see brief NN", consult the corresponding file for the
full code samples, DDL, diagrams, and rationale. This synthesis is the index.

---

## 1. What we're building (recap)

A hosted research-agent service consumed by analysts via web chat. The agent
calls MCP tools on `kloc-intelligence` (the existing stateful code-intelligence
service, 22 stdio MCP tools), delegates to sub-agents, loads Anthropic Skills
on demand, streams reasoning live, and persists everything for resume.

Three tiers + persistence:

- **Frontend** — Next.js + CopilotKit. View over the backend session.
- **Backend** — FastAPI + asyncio. Persistence + orchestration. **Does not run
  the agent loop, does not call the model, does not call MCP directly.**
- **Per-session runner** — ephemeral Strands process. Loads skills, opens MCP
  (stdio), streams AG-UI events back, posts hook webhooks.
- **Storage** — Postgres for sessions/messages/audit/artifact-metadata; MinIO
  (S3 API) for artifact files.

The brief's "hooks-as-policy-layer" rule means anything that smells like
authorization runs through a Strands hook callback — never through the agent's
prompt or tools.

---

## 2. Locked decisions

### 2.1 Stack picks (resolves the "to choose" list in poc.md)

| Concern | Decision | Why | Source |
|---|---|---|---|
| Runner isolation | **Docker per session, one mode** via `aiodocker` (PoC + prod) | Per-user: subprocess dropped. Production parity from day one, no macOS `PR_SET_PDEATHSIG` fragility, single transport, Docker is already required by `kloc-symfony` so the dev dependency is already paid. | user decision |
| Runner interface | Concrete `DockerRunner` class. `Runner` Protocol kept as a one-line seam for test fakes; ABC abstraction deferred until a real second impl is needed | YAGNI; one impl, but tests still want a mock | user decision |
| Bedrock AgentCore | **out of scope** (per user) | — | poc.md update |
| Wire protocol | **AG-UI 0.1.18** over **SSE** | `EventEncoder` only emits SSE; CopilotKit's `HttpAgent` consumes SSE; `EventSource` reconnect maps to our resume story | 02 |
| Strands→AG-UI adapter | **`ag_ui_strands.StrandsAgent`** (`agent.run(RunAgentInput) → async generator`) | Official; the FastAPI helper just SSE-encodes | 02 |
| Frontend framework | **Next.js 16 + CopilotKit 1.52.1** (`useCoAgent`, `useCopilotAction`, `useRenderToolCall`) | Repo 4's canonical wiring; productivity wins on shared state + frontend tools | 02, 05 |
| Backend framework | **FastAPI + asyncio**, `pydantic-settings` for env | Unanimous across reference repos; pydantic-settings diverges (none of the 4 repos use it) but it's right | 02, 05 |
| ORM + driver | **SQLAlchemy 2.0 async** + `postgresql+asyncpg` | First-class async, Alembic-native, Postgres-feature-rich | 04 |
| Migrations | **Alembic async** run by a **one-shot `backend-migrate` compose service** | Avoids replica races; idempotent | 04 |
| Object storage | **MinIO** (S3 API, locked) | per user | poc.md update |
| S3 client | **`aioboto3`** with **lifespan-managed client** on `app.state.s3` | Native async, boto3 ergonomics, MinIO-compatible | 04 |
| Streaming write strategy | **batched UPDATE, 256-char / 250-ms debounce**, server-side `content = content \|\| $1` | Avoids per-token vacuum thrash without a chunks table | 04 |
| Object-key layout | `sessions/{session_id}/artifacts/{artifact_id}/{filename}` | Prefix-delete by session; never rename for dedup | 04 |
| Bucket bootstrap | **`mc-init` sidecar** in compose (`mc mb --ignore-existing`) | MinIO docs recommend; runs once at compose-up | 04 |
| Skills system | **`strands_agentskills`** (`discover_skills` + `generate_skills_prompt`) + `SKILL.md` directories under `./skills/` | Native Strands integration of the Anthropic Skills spec | 01 |
| Sub-agent pattern | **agents-as-tools** (`tools=[summarizer_agent]`) | Reuses tool-call lifecycle so the audit hook fires for delegation too | 01 |
| Hook pattern (audit) | **`BeforeToolCallEvent`** in-process callback; the callback **wraps `httpx`** to POST a backend webhook | Strands hooks are in-process only — there is no SDK webhook dispatcher | 01 |
| Hook webhook transport | **HTTPS POST** `/v1/webhooks/runners/{runner_id}/events` with **HMAC-SHA256** over `timestamp + body` | Synchronous for `Before*` (policy gate), fire-and-forget for `After*` | 02 |
| Runner ↔ backend IPC | **JSONL wire format**. Outbound: chunked HTTPS POST `/internal/sessions/{id}/events`. Inbound: runner long-polls `GET /internal/sessions/{id}/inbox`. Hooks: separate HTTPS POST `/v1/webhooks/runners/{id}/events` (HMAC) | All runner→backend; no inbound ports on the container | 03 + user (Docker-only) |
| Session manager (Strands) | **don't pass one** to `Agent()` — backend is the canonical store | We rehydrate prior messages via `RunAgentInput.messages`; the adapter rebuilds Strands' internal history. **Warm-idle eviction is safe because of this.** | 01, 02 |
| Hydration channel | **mounted JSON file** at `KLOC_HYDRATION_PATH=/run/kloc/hydration.json`, bind-mounted read-only by the backend at spawn time | One mode (Docker-only) | 03 |
| Eviction policy | **warm-idle**: per-session `asyncio.Task` terminates the container `RUNNER_WARM_IDLE_S=60` after each `RUN_FINISHED`. Cancelled if a new user message arrives. Plus **heartbeat-dead** (30s no heartbeat → crashed). **No auto-restart**. | Covers conversational rhythm without wasting resources; rehydrate-on-resume makes kill-freely safe | user + 03 |
| Same-chat resume after warm-idle | Backend re-spawns a fresh container with `HydrationPayload(prior_messages=<full DB history>, state=<last STATE_SNAPSHOT>, ...)`; `ag_ui_strands.StrandsAgent` rebuilds Strands' internal history from `RunAgentInput.messages`. LLM sees no seam. | Postgres is the entire durable surface; runner has nothing to lose | user + 02 |
| Heartbeat | **runner emits every 15s**, even when idle | TCP keep-alive is too slow to detect a stuck runner | 03 |
| Cold-start cost | ~1–2 s on the first message after warm-idle expiry (image already pulled). Acceptable for occasional cold starts; tune `RUNNER_WARM_IDLE_S` higher if it becomes a UX problem | Single Docker daemon, prewarmed image, lazy Neo4j connect | new |
| Stream resilience | **`ExecutionRegistry` pattern from repo 2** — buffer events on the backend keyed by `(session_id, run_id)`, support cursor-replay on reconnect | Long runs survive client disconnect; analyst comes back, picks up where they left off | 02, 05 |
| Observability | **OTel auto-instrument** via `opentelemetry-instrument` in the Dockerfile CMD; OTLP env-driven exporter | Easy, no SDK changes; flip OTLP target to Langfuse later if we want LLM-debug UX | 01, 05 |
| MCP transport for kloc-intelligence | **stdio JSON-RPC 2.0** (existing contract) spawned by `MCPClient` inside the runner | Matches the existing kloc-intelligence MCP server; point-to-point, dies with the runner | 01, P1 |
| Multi-provider model switch | env var `LLM_PROVIDER=anthropic\|openrouter\|bedrock` driving a model factory | Repo 3's pattern; **Anthropic explicit** is required on dev (Bedrock is Strands' silent default and will fail without AWS creds) | 01, 05 |
| Test layout | **`tests/{unit,integration,e2e}`** + `pytest.ini` markers + `tests/fixtures/` (mock_model_provider, mock_session_manager, mock_tools) | Lift wholesale from repo 2 | 05 |

### 2.2 Deferred (not in PoC)

| Topic | Why deferred |
|---|---|
| Bedrock AgentCore runner | out of scope |
| Multi-tenant auth | PoC is single hardcoded analyst |
| Langfuse | OTel is enough until we want LLM-debug UX |
| Lifecycle rules on artifacts | PoC artifacts live forever |
| Graph / swarm multi-agent | agents-as-tools covers the PoC criterion; revisit when ≥ 3 fixed sub-agents need deterministic routing |
| Branching messages | `messages.parent_message_id` is in the schema; UI doesn't expose it yet |
| Soft delete vs hard delete | schema supports either; pick later |
| Token-window summarization for hydration | runner trusts the payload it gets; backend can trim later |

---

## 3. Module layout

Following repo 2/3 conventions with `src/` as the package root, file-per-route
in `src/api/`, file-per-agent in `src/agent/agents/` (we don't split into
`src/agents/` *and* `src/agent/` — that's the one thing repo 2 got wrong).

```
kloc-agent/
├── docker-compose.yml                  # postgres + minio + mc-init + backend-migrate + backend
├── docker-compose.dev.yml              # local overrides (volumes, hot reload, MCP local stdio)
├── Dockerfile                          # backend image; CMD = opentelemetry-instrument uvicorn ...
├── pyproject.toml                      # uv-managed
├── uv.lock
├── alembic.ini
├── .env.example
├── migrations/
│   ├── env.py                          # async, SQLAlchemy 2.0
│   └── versions/
├── docs/
│   ├── investigation.md                # this file
│   └── research/                       # 5 per-stream briefs (verbatim)
├── skills/                             # SKILL.md directories — Anthropic Skills convention
│   └── <skill-name>/SKILL.md
├── src/
│   ├── main.py                         # FastAPI app, lifespan(s3, db, runner_registry, sweeper)
│   ├── settings.py                     # pydantic-settings — single Settings class
│   ├── api/                            # file-per-route, repo 3 convention
│   │   ├── sessions.py                 # POST /v1/sessions, GET /v1/sessions/{id}, /messages
│   │   ├── stream.py                   # GET/POST /v1/sessions/{id}/stream  (SSE)
│   │   ├── artifacts.py                # GET /v1/artifacts/{id} → presigned URL
│   │   ├── webhooks.py                 # POST /v1/webhooks/runners/{id}/events  (HMAC, sync policy)
│   │   ├── internal.py                 # POST /internal/sessions/{id}/events  (runner JSONL ingress)
│   │   ├── health.py                   # GET /healthz, /readyz
│   │   └── stop.py                     # POST /v1/sessions/{id}/runs/{run_id}/cancel
│   ├── db/
│   │   ├── base.py                     # DeclarativeBase + MetaData(naming_convention=...)
│   │   ├── engine.py                   # create_async_engine, async_sessionmaker
│   │   ├── deps.py                     # get_session()
│   │   └── models.py                   # Session, Message, AuditLog, ArtifactMetadata
│   ├── repos/                          # repository layer (thin)
│   │   ├── sessions.py
│   │   ├── messages.py
│   │   ├── audit.py
│   │   └── artifacts.py
│   ├── storage/
│   │   └── s3.py                       # aioboto3 helpers: upload_bytes, presigned_get
│   ├── streaming/
│   │   ├── execution_registry.py       # repo 2 lift — (session_id, run_id) → buffer + cursor
│   │   ├── agui_event_formatter.py     # repo 2 lift — Strands → AG-UI mapping
│   │   ├── event_bus.py                # in-process pub/sub keyed by session_id (in-proc only)
│   │   └── sse.py                      # encoder + StreamingResponse glue
│   ├── runner_mgmt/
│   │   ├── protocol.py                 # Runner Protocol + HydrationPayload (test seam only)
│   │   ├── docker_runner.py            # DockerRunner — aiodocker; ONLY runner mode
│   │   ├── registry.py                 # dict[session_id, RunnerHandle]
│   │   ├── warm_idle.py                # per-session WarmIdleManager (asyncio.Event-driven)
│   │   ├── heartbeat.py                # per-session heartbeat watcher (30s timeout)
│   │   └── hydrate.py                  # write HydrationPayload → tempfile for bind-mount
│   ├── hooks_audit/                    # backend-side helpers for the hook webhook receiver
│   │   ├── verify_hmac.py
│   │   └── policy.py                   # placeholder — PoC: allow all, log only
│   ├── skill_loader/                   # if we wrap repo 2's skill/ layer
│   └── tools/                          # backend-side helper tools (sparse for PoC)
├── runner/                             # CODE THAT RUNS INSIDE EACH RUNNER PROCESS
│   ├── __main__.py                     # entrypoint; reads hydration, builds Agent, runs loop
│   ├── agent_factory.py                # create_agent(payload) — per-session, fresh
│   ├── model_factory.py                # LLM_PROVIDER switch (anthropic|openrouter|bedrock)
│   ├── hooks/
│   │   ├── audit.py                    # BeforeToolCallEvent → POST backend webhook
│   │   └── utils.py                    # resolve_tool_call (lifted from repo 2)
│   ├── mcp_clients.py                  # build stdio MCPClient for kloc-intelligence
│   ├── channel.py                      # HTTP channel: emit (chunked POST) + iter_inbound (long-poll)
│   └── skills/                         # mount: /skills:ro from host
├── frontend/                           # Next.js 16 + CopilotKit 1.52.1 + AG-UI 0.0.42
│   ├── Dockerfile
│   ├── next.config.ts
│   ├── package.json
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx              # <CopilotKit runtimeUrl="/api/copilotkit" agent="kloc_agent">
│   │   │   ├── page.tsx                # main chat; useCoAgent, useFrontendTool, useRenderToolCall
│   │   │   └── api/
│   │   │       ├── copilotkit/route.ts # CopilotRuntime + HttpAgent → agent-proxy
│   │   │       └── agent-proxy/route.ts# builds AG-UI RunAgentInput envelope, proxies SSE
│   │   ├── components/
│   │   ├── hooks/                      # optional repo 2 lift for non-Copilot routes
│   │   ├── lib/api.ts                  # session lifecycle REST
│   │   └── utils/sseParser.ts          # repo 2 lift; useful when not going through CopilotRuntime
└── tests/
    ├── conftest.py
    ├── fixtures/
    │   ├── mock_model_provider.py
    │   ├── mock_session_manager.py
    │   └── mock_tools.py
    ├── pytest.ini                      # asyncio_mode=auto; markers unit, integration, e2e
    ├── unit/
    ├── integration/
    └── e2e/
        └── sse_client.py               # repo 2 lift
```

Notes:

- **`src/` is the backend; `runner/` is what the runner spawns.** They live in
  the same monorepo so they can share `db/models.py` and a few typed payloads,
  but the runner image installs `runner/` only (slimmer image, less attack
  surface).
- **`mcp-servers/` is intentionally absent.** kloc-intelligence already
  publishes its MCP surface as `uv run kloc-intelligence mcp-server`; we just
  point the runner at it. We'll add `mcp-servers/` later if/when we own a
  bespoke MCP server.

---

## 4. Database schema (locked)

Four tables, three named enums-as-text. Full DDL is in brief 04 §1; what
follows is the canonical shape.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    analyst_id    text        NOT NULL,
    title         text        NOT NULL DEFAULT 'Untitled session',
    metadata      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    closed_at     timestamptz
);
CREATE INDEX ix_sessions_open
    ON sessions (analyst_id, updated_at DESC) WHERE closed_at IS NULL;
CREATE INDEX ix_sessions_metadata_gin
    ON sessions USING gin (metadata jsonb_path_ops);

CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system', 'tool');

CREATE TABLE messages (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id        uuid        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role              message_role NOT NULL,
    content           text        NOT NULL DEFAULT '',
    content_parts     jsonb,
    parent_message_id uuid        REFERENCES messages(id) ON DELETE SET NULL,
    model             text,
    token_count       integer,
    created_at        timestamptz NOT NULL DEFAULT now(),
    finalized_at      timestamptz,
    seq               bigint      NOT NULL
);
CREATE UNIQUE INDEX uq_messages_session_seq ON messages (session_id, seq);
CREATE INDEX ix_messages_streaming
    ON messages (session_id) WHERE finalized_at IS NULL;

CREATE TABLE audit_log (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid        REFERENCES sessions(id) ON DELETE CASCADE,
    message_id    uuid        REFERENCES messages(id) ON DELETE SET NULL,
    event_type    text        NOT NULL,
    actor         text        NOT NULL,
    payload       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_session_created ON audit_log (session_id, created_at);
CREATE INDEX ix_audit_payload_gin     ON audit_log USING gin (payload jsonb_path_ops);

CREATE TABLE artifact_metadata (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid        NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_id    uuid        REFERENCES messages(id) ON DELETE SET NULL,
    filename      text        NOT NULL,
    content_type  text        NOT NULL,
    size_bytes    bigint      NOT NULL,
    bucket        text        NOT NULL,
    object_key    text        NOT NULL,
    sha256        bytea       NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_artifact_session_object
    ON artifact_metadata (session_id, object_key);
```

Key contract rules:

- **`seq`** (per-session monotonic) is canonical message order. Wall-clock
  ordering on `created_at` is informational only.
- **`finalized_at IS NULL`** ⇔ stream in flight. On backend boot we scan this
  set and either reattach or close out with `audit_log.event_type = 'stream_orphaned'`.
- **`UNIQUE (session_id, object_key)`** absorbs duplicate artifact webhooks.
- **`event_type text`** (not enum): audit taxonomy grows; an enum forces a
  migration per new event. App-side `Literal[...]` keeps the surface narrow.

---

## 5. REST + streaming API (locked shape)

Full prose in brief 02 §6.

```
# Session lifecycle (JSON)
POST   /v1/sessions                        → 201 {session_id, created_at}
GET    /v1/sessions/{id}                   → 200 {id, status, runner_state, message_count, ...}
GET    /v1/sessions/{id}/messages?after=cursor&limit=100
POST   /v1/sessions/{id}/messages          → 202 {run_id, stream_url}
POST   /v1/sessions/{id}/close             → 204
POST   /v1/sessions/{id}/runs/{run_id}/cancel  → 204

# Streaming (SSE — text/event-stream)
GET    /v1/sessions/{id}/stream?run_id=...&last_event_id=...
POST   /v1/sessions/{id}/stream            (CopilotKit HttpAgent calls this with RunAgentInput)

# Hook webhooks (from runners to backend)
POST   /v1/webhooks/runners/{runner_id}/events
       Authorization: HMAC <sig>
       X-Kloc-Hook-Event: BeforeToolCall
       X-Kloc-Hook-Ts: 1747200001234
       → 202 {decision: "allow" | "deny", reason?: "..."}

# Internal — runner streams events back to backend (Docker mode only)
POST   /internal/sessions/{id}/events      Transfer-Encoding: chunked
                                           body: JSONL stream of AG-UI events

# Internal — backend tells runner about new user messages (Docker mode)
GET    /internal/sessions/{id}/inbox       long-poll JSON  (runner's tiny in-container server)

# Artifacts
GET    /v1/artifacts/{id}                  → 302 to presigned MinIO URL
```

Persistence ordering invariants (re-emphasized; brief 02 §9):

1. User message: **persist + commit, then forward** to runner. Never the other way.
2. Each `TEXT_MESSAGE_CONTENT.delta`: append to the persistent message buffer
   (batched: 256 chars / 250 ms). On `TEXT_MESSAGE_END`: set `finalized_at`.
3. `TOOL_CALL_END`: persist `AssistantMessage(tool_calls=[...])`.
4. `TOOL_CALL_RESULT`: persist `ToolMessage`.
5. `STATE_SNAPSHOT`: persist snapshot. `STATE_DELTA`: apply to in-memory + persist deltas.
6. Hook webhooks: **persist audit row before responding** `{decision:"allow"}`.

---

## 6. Minimum vertical PoC slice

Every poc.md success criterion, hit exactly once:

```
1. Analyst opens https://kloc-agent.local
2. Frontend POST /v1/sessions  → backend persists  → 201 {session_id}
                                                       ↳ AUDIT runner_lifecycle:session_opened
3. Analyst types "Find handlers of OrderPlaced and summarise them."
4. Frontend (CopilotKit) → /api/copilotkit → CopilotRuntime → /api/agent-proxy
   Proxy builds RunAgentInput {thread_id:session_id, run_id, messages:[user msg],
       state:{}, tools:[<frontend-only tools>], context:[]}
5. POST /v1/sessions/{id}/stream (body = RunAgentInput, Accept: text/event-stream)
   ↳ Backend persists UserMessage, commits.
   ↳ Backend asks RunnerRegistry → DockerRunner.spawn(HydrationPayload(
        session_id, system_prompt, prior_messages=<full DB history>,
        state=<last STATE_SNAPSHOT>,
        mcp_endpoints=[stdio: 'uv run kloc-intelligence mcp-server --database demo'],
        skills_dir="/skills",
        model_id="anthropic/claude-sonnet-4-6"))
   ↳ Backend writes hydration to /tmp/hydration-<rid>.json, bind-mounts
     read-only into the container at /run/kloc/hydration.json.
   ↳ Runner reads the file → builds Strands Agent:
        with MCPClient(stdio_client(StdioServerParameters('uv', ['run','kloc-intelligence','mcp-server','--database','demo']))) as kloc_mcp:
            tools = kloc_mcp.list_tools_sync()
            skills = discover_skills('./skills')
            summarizer = Agent(model=..., name="summarizer",
                               system_prompt="3-bullet executive summary.")
            agent = Agent(model=AnthropicModel(model_id="claude-sonnet-4-6"),
                          system_prompt=base_prompt + generate_skills_prompt(skills),
                          tools=[*tools, summarizer])
            agent.hooks.add_callback(BeforeToolCallEvent, audit_callback)  # POSTs hook webhook
6. Strands agent loop:
   - emits AG-UI events (TEXT_MESSAGE_*, TOOL_CALL_*, STATE_*) via ag_ui_strands.StrandsAgent
   - LLM calls kloc_context (MCP tool)   ← satisfies "uses ≥1 MCP tool"
        hook → POST /v1/webhooks/runners/{rid}/events {BeforeToolCall}  ← satisfies "audit log"
   - kloc-intelligence stdio → JSON-RPC result
   - LLM loads "summarization-style" skill via file_read         ← satisfies "≥1 skill"
   - LLM delegates to summarizer sub-agent                       ← satisfies "≥1 sub-agent"
        hook → POST /v1/webhooks/runners/{rid}/events {BeforeToolCall name=summarizer}
   - LLM finishes; RUN_FINISHED emitted
7. Runner JSONL events → backend via chunked HTTPS POST /internal/sessions/{id}/events
   Backend ExecutionRegistry buffers each event keyed by (session_id, run_id)
   For each event:
     - persist (batched debounce for text deltas)               ← satisfies "persisted"
     - SSE-encode and yield to /v1/sessions/{id}/stream         ← satisfies "streams reasoning live"
8. Frontend EventSource (inside CopilotKit) renders text deltas + tool-call cards
9. RUN_FINISHED → backend.WarmIdleManager.on_run_finished()
   starts the 60-second warm-idle timer.
10. CASE A — follow-up within 60s:
       analyst types again → backend.WarmIdleManager.on_user_message()
       cancels the timer → SAME container handles the follow-up (no respawn)
11. CASE B — no follow-up within 60s:
       timer expires → DockerRunner.terminate(handle) → container stopped + removed
       audit_log: runner_warm_idle_evicted
12. CASE C — analyst returns next day, same chat:
       Frontend opens session → GET /v1/sessions/{id} + GET /…/messages
                                ← satisfies "session persists"
       Analyst types new message → POST /v1/sessions/{id}/stream
       Backend sees no live container for this session_id →
           reads full message history from Postgres →
           reads last STATE_SNAPSHOT from audit_log →
           DockerRunner.spawn(HydrationPayload(prior_messages=..., state=..., ...))
       Strands adapter rebuilds Agent.messages from RunAgentInput.messages
           → LLM sees full prior conversation → continues naturally
       Cost: ~1-2s cold start; analyst experiences zero seam.
```

What this PoC slice **does not** include:

- Docker runner (subprocess only)
- Multi-tenant auth (one hardcoded analyst token)
- Artifact uploads (MinIO is plumbed, but the PoC agent doesn't generate files)
- Langfuse (OTel console exporter is fine)
- Frontend tool actions beyond the default render
- Policy enforcement in hooks (audit-only)
- Eviction sweeper running in earnest (we'll have it; idle timeout 15 min)

---

## 7. Risk inventory

Ordered by likelihood × impact for PoC, not theoretical worst-case.

| # | Risk | Where it bites | Mitigation |
|---|------|----------------|------------|
| R1 | **`agentskills` not on PyPI** — installs only from git | Build reproducibility | Pin in `pyproject.toml` to a specific commit hash: `agentskills @ git+https://github.com/aws-samples/sample-strands-agents-agentskills@<sha>`. Vendor if API churns. |
| R2 | **Strands silently defaults to Bedrock** without `model=` | Will fail on any dev machine without AWS creds | Always construct `AnthropicModel(...)` explicitly. Document in CLAUDE.md (kloc-agent local). |
| R3 | **`MESSAGES_SNAPSHOT` quadratic bandwidth** on long sessions | Cost + latency for sessions with many tool calls | Brief 02 §10 flags this. Workaround: `StrandsAgentConfig.emit_messages_snapshot=False` and reconstruct on the client. Not a PoC concern; add to backlog. |
| R4 | **Mid-flight tool call when runner crashes** | Tool may be non-idempotent (especially future write-tools) | Mark `tool_call.crashed` in audit; do **not** auto-replay; surface "session interrupted, click to retry" to the analyst. Brief 03 §7. |
| R5 | **AG-UI has no semver** | Hard to express compatibility | Pin `ag-ui-protocol==0.1.18` and `@ag-ui/client@0.0.42` exactly; document the pair as our "AG-UI baseline." Brief 02 §10. |
| R6 | **Docker daemon is now a hard dependency on dev hosts** | Can't run kloc-agent without Docker | Acceptable — already required by `kloc-symfony`; document in README; macOS users already have Docker Desktop. |
| R6b | **Warm-idle race: user message arrives during `container.stop()`** | The kill is mid-flight when a new message tries to send | `WarmIdleManager.on_user_message()` must `await self._task` (the kill task) before deciding spawn-vs-reuse; if it sees the container already terminated, fall through to fresh-spawn-with-hydration. Treated as a normal cold-start. |
| R6c | **Cold-start latency on warm-idle resume** (~1–2 s) | First token after the 60-second window has a perceptible delay | Tune `RUNNER_WARM_IDLE_S` per UX feedback. Image-prewarm spare ready container is the escape hatch (not for PoC). |
| R6d | **Skills mount drift** between spawns | A skill present on first spawn but removed before the resume spawn confuses the LLM that referenced it | Skills mount is the same path (`/skills:ro`) on every spawn; document that mutations to `./skills/` should drop in-flight sessions. Future: snapshot skills set into hydration. |
| R7 | **`CopilotKit HttpAgent` doesn't natively forward custom headers** | Auth between Next.js BFF and backend may need a service-account model | Auth at the Next.js route layer; backend trusts the BFF. Brief 02 §10. |
| R8 | **Hook webhook deadline (2 s) + backend slowness** | Could starve agent runs | `Before*` hooks deny-by-default after 2 s; `After*` hooks fire-and-forget with bounded async queue (256), drop heartbeats first if it fills. Emit a `CustomEvent(name="HookBackpressure")` so the audit chain has a record. Brief 02 §8. |
| R9 | **Concurrent backend replicas race on migrations** | Conflicting Alembic upgrades | One-shot `backend-migrate` compose service with `depends_on: { condition: service_completed_successfully }`. Brief 04 §4.4. |
| R10 | **Orphan MinIO uploads** (runner uploads, webhook never lands) | Storage leak | Nightly orphan sweep: list `sessions/{sid}/artifacts/*` and delete objects with no `artifact_metadata` row + older than 24 h. Brief 04 §8.3. |
| R11 | **`MCPClient` context-manager exit kills the subprocess** | Easy to drop out of scope and break the next tool call | Wrap the entire session lifetime in the `with` block. Document in runner code. Brief 01 §6. |
| R12 | **Multiple backend replicas + in-process runner registry** | Replica A spawns a subprocess, replica B doesn't know about it | PoC is single-replica. Future: move registry to Postgres (or Redis); the `Runner` interface stays the same. |
| R13 | **`StrandsTelemetry` hook-event span coverage unverified** | Audit↔OTel correlation may miss hook lifecycle | Wire `setup_console_exporter()` first time, verify what spans fire, then OTLP. Brief 01 §10. |

---

## 8. Open questions (verify during implementation)

These don't block planning, but a flag here means the implementation plan
should call them out as explicit verification tasks.

- **`StrandsAgent.run(input)` event coverage in sub-agent (agents-as-tools) mode** — does the orchestrator's stream include the sub-agent's intermediate text deltas, or only the final tool result? Brief 01 §10. Test with `agent.stream_async()` early.
- **`HttpAgent` header forwarding** — confirm whether `headers` config exists on `@ag-ui/client@0.0.42` `HttpAgent`. Brief 02 §10.
- **`ag_ui_strands.StrandsAgentConfig.emit_messages_snapshot`** — does this flag exist on `0.1.8` and do exactly what we expect? Test before assuming we can flip it later.
- **MCP `list_tools_sync()` blocking the asyncio loop** — if so, use `list_tools_async()` in the runner. Brief 01 §10.
- **Subprocess `proc.stdin.write` line size limits** — `limit=1024 * 1024` set at spawn, but if the hydration payload (prior_messages) is large, single-line stdin may exceed it. Multi-line framing fallback? Brief 03.
- **`opentelemetry-instrument` vs explicit Strands `setup_otlp_exporter()`** — does auto-instrumentation cover Strands' custom spans, or do we still need to call `StrandsTelemetry().setup_otlp_exporter()` at runner boot? Test side-by-side.

---

## 9. What goes into the implementation plan next

These map directly to phase-2-style implementation tracks for the
`writing-plans` skill. Listed in dependency order (later items assume the
earlier ones exist).

### Track A — Infrastructure scaffold (one-shot, ~half-day)
- `pyproject.toml` (uv) with locked deps (Section 0)
- `docker-compose.yml` per brief 04 §10 + `.env.example`
- Alembic init + `backend-migrate` service
- `src/settings.py` (pydantic-settings)
- `src/main.py` with lifespan: engine, s3, runner_registry, eviction sweeper
- `Dockerfile` with `opentelemetry-instrument` CMD

### Track B — Persistence layer
- `src/db/models.py` with the 4 tables from §4
- Migration `2026_05_14_0001_init.py`
- `src/repos/*.py` (sessions, messages, audit, artifacts)
- Batched-write debounce helper for streaming UPDATEs (256-char / 250-ms)

### Track C — Backend HTTP surface
- `src/api/sessions.py` (lifecycle)
- `src/api/stream.py` (SSE generator with `request.is_disconnected()` + persist-then-yield)
- `src/api/webhooks.py` (HMAC verify + audit row + policy decision)
- `src/api/internal.py` (runner JSONL ingress for Docker mode — stub for PoC)
- `src/api/artifacts.py` (presigned URL)
- `src/streaming/execution_registry.py` (lift from repo 2, adapt session/run keying)
- `src/streaming/agui_event_formatter.py` (lift from repo 2)

### Track D — Runner (Docker-only) + warm-idle lifecycle
- `runner/Dockerfile` — Python 3.12-slim, uv-installed deps, `ENTRYPOINT ["python","-m","runner"]`, `VOLUME ["/skills"]`, `KLOC_HYDRATION_PATH=/run/kloc/hydration.json`
- `src/runner_mgmt/protocol.py` (one-line `Runner` Protocol — test seam only)
- `src/runner_mgmt/docker_runner.py` — full impl per brief 03 § 2 (aiodocker, bridge network, 1 GiB / 2 vCPU / 256 pids, `RestartPolicy: no`, `kloc.role=runner` label, `AutoRemove: false`)
- `src/runner_mgmt/hydrate.py` — write `HydrationPayload` → `/tmp/hydration-<rid>.json`; build bind-mount config; clean up tempfile on terminate
- `src/runner_mgmt/registry.py` — `dict[session_id, RunnerHandle]`
- `src/runner_mgmt/warm_idle.py` — `WarmIdleManager` per active session (asyncio.Event-driven, cancellable, default `RUNNER_WARM_IDLE_S=60`)
- `src/runner_mgmt/heartbeat.py` — per-session watcher; on 30 s no heartbeat → terminate + mark `crashed`
- `src/main.py` lifespan: register a boot-time **sweeper** that does `docker ps --filter label=kloc.role=runner` and kills any orphan containers from previous backend runs
- `runner/__main__.py` (reads hydration file, builds Agent, runs inbound long-poll loop, emits heartbeats)
- `runner/agent_factory.py` (per-session, fresh — repo 2's factory shape)
- `runner/hooks/audit.py` (BeforeToolCallEvent → `httpx.AsyncClient.post` to backend with HMAC)
- `runner/hooks/utils.py:resolve_tool_call` (lift from repo 2)
- `runner/mcp_clients.py` (stdio MCPClient for kloc-intelligence)
- `runner/channel.py` (chunked POST out, long-poll inbound, heartbeat task)

### Track E — Skills wiring
- `skills/` directory with one demo `SKILL.md` (e.g. "summarization-style")
- Runner-side `discover_skills` + `generate_skills_prompt` in `agent_factory.py`
- Verify progressive disclosure works (LLM uses `file_read` to load body)

### Track F — Frontend
- `frontend/` scaffolded from `CopilotKit/with-strands-python` (or repo 4's demo)
- `src/app/layout.tsx` with `<CopilotKit runtimeUrl="/api/copilotkit" agent="kloc_agent">`
- `src/app/page.tsx` with `useCoAgent`, `useRenderToolCall` for MCP tool cards
- `src/app/api/copilotkit/route.ts` (CopilotRuntime + HttpAgent → agent-proxy)
- `src/app/api/agent-proxy/route.ts` (lift from repo 4 — builds AG-UI envelope, proxies SSE to backend)
- `src/lib/api.ts` for session lifecycle REST

### Track G — Tests
- `pytest.ini` (lift from repo 2)
- `tests/conftest.py` + `tests/fixtures/` (lift from repo 2)
- `tests/unit/`: schema, repo, debounce-buffer, HMAC verify
- `tests/integration/`: subprocess runner spawn + JSONL roundtrip, SSE encoder
- `tests/e2e/sse_client.py`: full vertical slice covering §6

### Track H — Observability (light)
- Wire `StrandsTelemetry().setup_console_exporter()` in the runner for dev
- OTLP env vars documented; `opentelemetry-instrument` already in Dockerfile

### Out of plan (later)
- DockerRunner (Track D variant)
- Multi-tenant auth
- Langfuse exporter
- Frontend tools beyond default render
- Policy decisions in hooks (non-audit)
- Branching UI

---

## 10. Handoff to `writing-plans`

This investigation is the spec. Next step: invoke the `writing-plans` skill
with this file + the 5 briefs as inputs to produce a concrete, ordered
implementation plan (with acceptance criteria per track and explicit test
cases).

Suggested invocation:

> Use the `writing-plans` skill to produce a plan for kloc-agent. The
> investigation synthesis is `docs/investigation.md`. The supporting research
> briefs are in `docs/research/`. Implement in track order A → B → C → D → E →
> F → G → H, with the vertical slice in §6 as the integration target for the
> first runnable demo.

---

## Appendix — file map of artifacts produced this pass

```
kloc-agent/
├── poc.md                                  # original project brief (unchanged)
└── docs/
    ├── investigation.md                    # this file
    └── research/
        ├── 01-strands-minimal.md
        ├── 02-backend-agui.md
        ├── 03-runner-mgmt.md
        ├── 04-persistence-storage.md
        └── 05-reference-projects.md
```
