# kloc-agent — Architecture diagrams

> Three views of the same system: zoom-out (1) → zoom-in on the backend (2) →
> zoom-in on the runner / agent (3). Diagram conventions:
>
> ```
> ─►  call (request)         ◄───►  bidirectional
> ═►  stream (SSE / JSONL)   ┊  spawn / lifecycle ownership
> ```

---

## 1 — System-level: UI → Backend → Runner → MCP / Storage / LLM

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                       BROWSER (analyst)                                │
│  React • CopilotKit components: <CopilotSidebar>, useCoAgent, useRenderToolCall       │
└──────────────────────────┬─────────────────────────────────────────────────────────────┘
                           │  HTTPS POST (JSON)            EventSource / SSE
                           │  send user message            stream agent events
                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          NEXT.JS FRONTEND  (Node 20, port 3000)                        │
│  ┌──────────────────────────────┐    ┌────────────────────────────────────────────┐   │
│  │ src/app/page.tsx             │    │ src/app/api/copilotkit/route.ts            │   │
│  │  <CopilotKit                 │    │  CopilotRuntime(                           │   │
│  │   runtimeUrl="/api/copilotkit"│   │    agents: { kloc_agent: HttpAgent({url})  │   │
│  │   agent="kloc_agent">        │    │  })                                        │   │
│  └──────────────────────────────┘    └─────────────────────────┬──────────────────┘   │
│                                                                 │                      │
│                                      ┌──────────────────────────▼─────────────────┐   │
│                                      │ src/app/api/agent-proxy/route.ts           │   │
│                                      │  builds AG-UI RunAgentInput envelope       │   │
│                                      │  fetches BACKEND_URL, proxies SSE bytes    │   │
│                                      └─────────────────────────┬──────────────────┘   │
└────────────────────────────────────────────────────────────────┼───────────────────────┘
                                                                 │
                       HTTPS POST RunAgentInput + Accept SSE     │
              ╔══════════════════════════════════════════════════╝
              ║   SSE: data:{AG-UI event}\n\n  (33 event types, ag-ui-protocol 0.1.18)
              ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                          BACKEND  FastAPI (Python 3.12, port 8000)                     │
│                                                                                        │
│   /v1/sessions/*         /v1/sessions/{id}/stream    /v1/webhooks/runners/{id}/events  │
│   (CRUD, JSON)           (SSE in & out)              (HMAC-signed audit + policy)      │
│                                                                                        │
│   ┌────────────────────────────────────────────────────────────────────────────────┐  │
│   │  ExecutionRegistry  ◄──── decouples client connection from runner lifetime      │  │
│   │  buffers events keyed by (session_id, run_id) + cursor replay on reconnect      │  │
│   └────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                        │
│   ┌────────────────────────────────────────────────────────────────────────────────┐  │
│   │  RunnerRegistry   dict[session_id, RunnerHandle]  +  asyncio eviction sweeper   │  │
│   └────┬───────────────────────────────────────────────────────────────────────────┘  │
└────────┼───────────────────────────────────────────────────────────────────────────────┘
         │                          ▲          ▲          ▲
         │ ┊ docker create/start/    │          │          │
         │   attach/wait/delete       │          │          │
         │   (aiodocker)              │          │ HTTPS    │ HTTPS POST  /v1/webhooks/...
         │                            │          │ long-poll│ Authorization: HMAC <sig>
         │                            │ chunked  │ GET      │ → returns {decision:"allow"|"deny"}
         │                            │ POST     │ /internal│ → backend persists audit row
         ▼                            │ JSONL    │ /inbox   │
┌──────────────────────────────────────────────────────────────────────────┐
│                  PER-SESSION RUNNER  (one container per session_id)       │
│                       DockerRunner   (aiodocker, one mode)                │
│        kloc-agent-runner:<sha>  •  1 GiB / 2 vCPU / 256 pids / no-restart │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │  Strands Agent  (anthropic/claude-sonnet-4-6)                        │ │
│  │    + MCPClient (stdio child)   + agents-as-tools sub-agent           │ │
│  │    + Skills (discover_skills + generate_skills_prompt)               │ │
│  │    + BeforeToolCallEvent hook (POSTs audit webhook)                  │ │
│  │    + ag_ui_strands.StrandsAgent  ── async generator of AG-UI events  │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└────────┬────────────────────────────────────┬──────────────────────────┬──┘
         ┊ spawn (stdio child)                │                          │
         ▼                                    │ HTTPS                    │ HTTPS
┌─────────────────────────────────┐           ▼                          ▼
│  kloc-intelligence              │  ┌──────────────────────┐  ┌──────────────────────┐
│  MCP server (stdio, JSON-RPC 2.0)│  │  Anthropic API        │  │  (future) OpenRouter │
│  22 tools: kloc_resolve,         │  │  claude-sonnet-4-6    │  │  / Bedrock           │
│  kloc_context, kloc_search, …    │  │                       │  │                      │
│  → Neo4j + Qdrant                │  └──────────────────────┘  └──────────────────────┘
└─────────────────────────────────┘
         ▲
         │ Cypher
         │
┌─────────────────────────────────┐
│   Neo4j 5  +  Qdrant            │  (owned by kloc-intelligence, not by us)
└─────────────────────────────────┘

                    ─── persistence (owned by backend) ───

         ┌──────────────────┐     ┌──────────────────────────────────┐
Backend  │  Postgres 16     │     │  MinIO (S3 API, port 9000)        │
  ◄────► │  sessions /      │     │  bucket: kloc-agent-artifacts-{env}│
asyncpg  │  messages /      │     │  key: sessions/{id}/artifacts/    │
         │  audit_log /     │     │       {artifact_id}/{filename}    │
         │  artifact_meta   │     │  + mc-init sidecar (creates bucket)│
         └──────────────────┘     └──────────────────────────────────┘
         (also: backend-migrate one-shot service runs alembic upgrade head)
```

**Read-it-in-one-paragraph.** The browser talks to the Next.js frontend
through CopilotKit. CopilotKit's `HttpAgent` POSTs to a Next.js API route
which proxies (wrapping payloads in the AG-UI `RunAgentInput` envelope) to
the FastAPI backend. The backend persists the user message, looks up or
spawns the session's **Docker container** (via `aiodocker`, on the compose
bridge network), and streams the runner's AG-UI events back to the browser
as SSE. The runner POSTs its event stream back to the backend as
chunked-HTTP JSONL, long-polls the backend for inbound user messages,
spawns `kloc-intelligence` as a stdio MCP child, calls Anthropic for the
LLM, posts hook audit webhooks, and dies when the session goes idle. State
lives in Postgres + MinIO; the runner is stateless. **Docker is required on
the dev host** (you already have it for `kloc-symfony`).

---

## 2 — Backend internal architecture: routes → services → repos → DB

```
                         ┌────────────────────────────────────┐
                         │       INCOMING (HTTPS / SSE)        │
                         └────────────────┬────────────────────┘
                                          │
              ╔══════════════════════════ │ ═══════════════════════════════╗
              ║       PRESENTATION         │   src/api/  (one file per route group)║
              ╠══════════════════════════ │ ═══════════════════════════════╣
              ║  ┌────────────┐  ┌────────▼──────────┐  ┌─────────────────┐ ║
              ║  │ sessions.py│  │ stream.py          │  │ webhooks.py      │ ║
              ║  │ POST/GET/  │  │ GET  /…/stream     │  │ POST /…/webhooks │ ║
              ║  │ DELETE     │  │ POST /…/stream     │  │ HMAC verify      │ ║
              ║  └─────┬──────┘  │ (SSE generator)    │  │ + audit row      │ ║
              ║        │         └────────┬───────────┘  └────────┬─────────┘ ║
              ║  ┌─────▼──────┐  ┌────────▼───────────┐  ┌────────▼─────────┐ ║
              ║  │ artifacts  │  │ internal.py        │  │ stop.py          │ ║
              ║  │ presigned  │  │ /internal/…/events │  │ /…/runs/{id}/    │ ║
              ║  │ URLs       │  │ (Docker mode IPC)  │  │  cancel          │ ║
              ║  └─────┬──────┘  └────────┬───────────┘  └────────┬─────────┘ ║
              ║        │                  │                       │           ║
              ║  ┌─────▼──────────────────▼───────────────────────▼─────────┐ ║
              ║  │ health.py    /healthz   /readyz                          │ ║
              ║  └────────────────────────────────────────────────────────-─┘ ║
              ╚════════│══════════════════│════════════════════════│══════════╝
                       │                  │                        │
              ╔════════│══════════════════│════════════════════════│══════════╗
              ║       SERVICE LAYER       │  src/services/  (orchestration)   ║
              ║  (thin — routes call directly when there's no orchestration)  ║
              ╠════════│══════════════════│════════════════════════│══════════╣
              ║        │           ┌──────▼─────────────────┐      │          ║
              ║        │           │ ChatService            │      │          ║
              ║        │           │  ─ persist user msg    │      │          ║
              ║        │           │  ─ get_or_spawn_runner │      │          ║
              ║        │           │  ─ stream_events       │      │          ║
              ║        │           │  ─ persist each event  │      │          ║
              ║        │           └─┬───────────┬──────────┘      │          ║
              ║        │             │           │                 │          ║
              ║  ┌─────▼─────┐ ┌─────▼─────┐ ┌──▼─────────────┐ ┌─▼────────┐ ║
              ║  │ Session   │ │ Streaming │ │ HookAudit      │ │ RunCancel│ ║
              ║  │ Service   │ │ Service   │ │ Service        │ │ Service  │ ║
              ║  └─────┬─────┘ └─────┬─────┘ └─────┬──────────┘ └─────┬────┘ ║
              ║        │             │             │                   │     ║
              ║        │             │             │                   │     ║
              ║   ┌────▼──────────-──▼──┐    ┌─────▼────────────┐      │     ║
              ║   │ Artifact Service    │    │ Policy (PoC: noop)│     │     ║
              ║   └────┬──────────────-─┘    └──────────────────┘      │     ║
              ╚═══════ │ ════════════════════════════════════════════ │══════╝
                       │                                              │
              ╔════════│══════════════════════════════════════════════│══════╗
              ║       INFRASTRUCTURE   src/streaming/, src/runner_mgmt/      ║
              ╠══════════════════════════════════════════════════════════════╣
              ║                                                              ║
              ║   ┌────────────────────────────┐   ┌──────────────────────┐  ║
              ║   │ ExecutionRegistry          │   │ RunnerRegistry       │  ║
              ║   │   buffers AG-UI events     │◄──┤   dict[session_id,   │  ║
              ║   │   per (session_id, run_id) │   │   RunnerHandle]      │  ║
              ║   │   cursor replay on resume  │   └─────────┬────────────┘  ║
              ║   └────────────────────────────┘             │               ║
              ║                                              │               ║
              ║   ┌──────────────────────────────────────────┘               ║
              ║   │                                                          ║
              ║   ▼  uses                                                    ║
              ║   ┌────────────────────────────────────────────┐             ║
              ║   │ Runner Protocol  (src/runner_mgmt/protocol.py)           ║
              ║   │   spawn / send_user_message / stream_events              ║
              ║   │   terminate / is_alive                                   ║
              ║   └────┬───────────────────────────┬───────────────┘         ║
              ║        │                           │                          ║
              ║   ┌────▼────────────┐         ┌────▼─────────────┐            ║
              ║   │ SubprocessRunner│         │ DockerRunner     │            ║
              ║   │ (PoC default)   │         │ (self-host prod) │            ║
              ║   │ asyncio         │         │ aiodocker        │            ║
              ║   └─────────────────┘         └──────────────────┘            ║
              ║                                                               ║
              ║   ┌────────────────────────────────────────────────────────┐  ║
              ║   │ EvictionSweeper  (asyncio task, 30 s tick, 15 min idle)│  ║
              ║   └────────────────────────────────────────────────────────┘  ║
              ║                                                              ║
              ║   ┌────────────────────┐    ┌───────────────────────────┐    ║
              ║   │ AGUIEventFormatter │    │ SSEEncoder + StreamingResp│    ║
              ║   │ Strands → AG-UI    │    │ persist-then-yield        │    ║
              ║   └────────────────────┘    └───────────────────────────┘    ║
              ╚══════════════════════════════════════════════════════════════╝
                                            │
              ╔═════════════════════════════│════════════════════════════════╗
              ║      DATA ACCESS            │      src/repos/                ║
              ╠═════════════════════════════│════════════════════════════════╣
              ║   ┌─────────────────────────▼──────────────────────────┐    ║
              ║   │ SessionRepo  /  MessageRepo  /  AuditRepo          │    ║
              ║   │ ArtifactRepo                                       │    ║
              ║   │ all consume AsyncSession injected via Depends      │    ║
              ║   └────────────────────────┬─────────────────────────-─┘    ║
              ╚═══════════════════════════ │ ═══════════════════════════════╝
                                           │
              ╔═════════════════════════════│════════════════════════════════╗
              ║       DB / STORAGE          │  src/db/, src/storage/         ║
              ╠═════════════════════════════│════════════════════════════════╣
              ║   ┌─────────────────────────▼──────────────────────────┐    ║
              ║   │ Async SQLAlchemy 2.0  +  asyncpg                   │    ║
              ║   │   engine.py: create_async_engine, async_sessionmaker│   ║
              ║   │   deps.py:  get_session()                          │    ║
              ║   │   models.py: Session, Message, AuditLog, ArtifactMD│    ║
              ║   └────────────────────────┬───────────────────────────┘    ║
              ║                            │                                ║
              ║                            ▼                                ║
              ║              ┌────────────────────────────┐                 ║
              ║              │   Postgres 16              │                 ║
              ║              └────────────────────────────┘                 ║
              ║                                                             ║
              ║   ┌──────────────────────────────────────────────────────┐  ║
              ║   │ src/storage/s3.py  ──  aioboto3 client (lifespan)    │  ║
              ║   │   app.state.s3                                        │  ║
              ║   │   upload_bytes / presigned_get                        │  ║
              ║   └────────────────────────────────┬────────────────────-─┘  ║
              ║                                    │                        ║
              ║                                    ▼                        ║
              ║                ┌──────────────────────────────────┐         ║
              ║                │  MinIO (S3 API)                  │         ║
              ║                └──────────────────────────────────┘         ║
              ╚═════════════════════════════════════════════════════════════╝
```

### 2.1 Request-flow recipes (concrete, not abstract)

**A. Send a user message (the hot path)**

```
POST /v1/sessions/{id}/stream  body: RunAgentInput
  │
  ▼ stream.py route
  │   1. validate RunAgentInput; pull last UserMessage out
  │   2. ChatService.start_run(session_id, user_msg)
  │        ├─ SessionRepo.touch(updated_at)
  │        ├─ MessageRepo.append(user_msg)              ← COMMIT before next step
  │        ├─ AuditRepo.append(event="user_message")
  │        ├─ RunnerRegistry.get_or_spawn(session_id)   ← may spawn SubprocessRunner
  │        └─ ExecutionRegistry.start_run(session_id, run_id)
  │   3. async generator:
  │        async for ev in runner.stream_events(handle):
  │          ExecutionRegistry.buffer(session_id, run_id, ev)
  │          MessageRepo.persist_event(ev)               ← batched UPDATE 256-char/250-ms
  │          AuditRepo.maybe_append(ev)                  ← certain event types only
  │          yield SSEEncoder.encode(ev)
  │   4. on RUN_FINISHED: ExecutionRegistry.finish_run(...); release runner (idle timer)
  ▼
StreamingResponse → SSE bytes back to browser
```

**B. Resume (analyst returns the next day)**

```
GET /v1/sessions/{id}                       → SessionRepo.get + counts
GET /v1/sessions/{id}/messages?after=...    → MessageRepo.list_after(cursor)
GET /v1/sessions/{id}/stream?last_event_id= → stream.py route:
                                                ExecutionRegistry.replay_from(cursor)
                                                  (events come from buffer or DB if buffer evicted)
                                                then live → if a new POST happens, normal hot path
```

**C. Hook webhook (runner → backend)**

```
POST /v1/webhooks/runners/{rid}/events
  HMAC <sig>  +  X-Kloc-Hook-Event: BeforeToolCall  +  body
  │
  ▼ webhooks.py route
  │   1. verify_hmac(body, sig, timestamp)
  │   2. HookAuditService.handle(event):
  │        ├─ AuditRepo.append(...)                     ← PERSIST BEFORE deciding
  │        ├─ Policy.decide(event) → "allow" | "deny"   (PoC: always "allow")
  │   3. return 202 {decision, reason?}
  ▼
runner reads decision, blocks tool call if "deny" via event.cancel_tool
```

---

## 3 — Runner / agent code organization (what lives inside one runner process)

```
                            ┌──────────────────────────────────┐
                            │   runner/__main__.py             │
                            │   (entrypoint; one per container)│
                            │                                  │
                            │   1. read hydration:             │
                            │       /run/kloc/hydration.json   │
                            │       (mounted by DockerRunner)  │
                            │   2. construct ag-ui adapter     │
                            │   3. construct Agent             │
                            │   4. open MCPClient (with-block) │
                            │   5. enter inbound message loop  │
                            └────────────────┬─────────────────┘
                                             │ uses
            ┌────────────────────────────────┼─────────────────────────────────┐
            │                                │                                 │
            ▼                                ▼                                 ▼
 ┌────────────────────────┐    ┌──────────────────────────────┐ ┌─────────────────────────┐
 │ runner/agent_factory.py │    │ runner/channel.py             │ │ runner/mcp_clients.py    │
 │                         │    │  (HTTP-only, talks to backend)│ │  build_kloc_mcp() →      │
 │ create_agent(payload)    │    │                               │ │   MCPClient(             │
 │  ─ pick model factory   │    │ ─ emit(event):  chunked POST  │ │     lambda: stdio_client(│
 │  ─ list skills           │    │   /internal/sessions/{id}/    │ │       StdioServerParams( │
 │  ─ build sub-agent       │    │   events  (JSONL body)        │ │         command="uv",    │
 │  ─ build orchestrator    │    │ ─ iter_inbound():             │ │         args=[...],      │
 │  ─ attach hooks          │    │   long-poll GET               │ │       )))                │
 │  ─ inject skills prompt  │    │   /internal/sessions/{id}/    │ └─────────────────────────┘
 │                         │    │   inbox  (returns user msgs)  │
 │                         │    │ ─ heartbeat() every 15 s      │
 └──────────┬──────────────┘    └──────────────────────────────┘
            │
            │ calls
            ▼
 ┌────────────────────────┐    ┌──────────────────────────┐
 │ runner/model_factory.py │    │ ag_ui_strands.StrandsAgent│ ← wraps Strands.Agent;
 │                         │    │   agent.run(RunAgentInput)│  yields AG-UI events as
 │ make_model(provider):   │    │   async generator         │  an async generator
 │   "anthropic" →         │    │                           │
 │     AnthropicModel(...) │    └──────────────────────────┘
 │   "openrouter" → ...    │                  ▲
 │   "bedrock" → ...       │                  │  wraps
 └────────────────────────┘                  │
                                              │
                  ┌──────────────────────────────────────────────┐
                  │   strands.Agent  (the orchestrator)           │
                  │                                               │
                  │   model      = AnthropicModel(claude-sonnet-4-6)
                  │   system_prompt = base_prompt                 │
                  │                   + generate_skills_prompt(skills)
                  │   tools      = [*mcp_tools, summarizer_subagent]│
                  │   hooks      = HookRegistry with               │
                  │                BeforeToolCallEvent → audit_cb   │
                  └──────────────────────────────────────────────┘
                          │             │              │
                          │             │              │
              ┌───────────┘             │              └────────────┐
              ▼                          ▼                           ▼
   ┌──────────────────┐      ┌──────────────────────┐    ┌──────────────────┐
   │ MCP TOOLS         │      │ SUB-AGENT (as tool)   │    │ HOOKS             │
   │                   │      │                       │    │                   │
   │ kloc_resolve      │      │ summarizer = Agent(   │    │ runner/hooks/     │
   │ kloc_context      │      │   name="summarizer",  │    │   audit.py        │
   │ kloc_usages       │      │   model=...,          │    │     async def     │
   │ kloc_deps         │      │   system_prompt="...")│    │     audit_cb(ev): │
   │ kloc_search       │      │ tools=[..., summarizer│    │       httpx.post( │
   │ kloc_inherit      │      │ ]                     │    │       backend/    │
   │ kloc_overrides    │      │                       │    │       webhook,    │
   │ kloc_flows        │      │ ── invoked via tool   │    │       hmac=...,   │
   │ kloc_messages     │      │ call by orchestrator  │    │       body=ev)    │
   │ kloc_events       │      │ ── BeforeToolCallEvent│    │   utils.py        │
   │ kloc_http_clients │      │ fires for sub-agent   │    │     resolve_tool_ │
   │ kloc_source       │      │ delegation, audit-free│    │     call(event)   │
   │ kloc_chunks       │      │                       │    │     ↳ unwraps     │
   │ … (22 total)      │      │                       │    │       skill_exec  │
   │                   │      │                       │    │       wrapper to  │
   │ ▼                 │      │                       │    │       see real    │
   │ each call →       │      │                       │    │       tool name   │
   │ JSON-RPC 2.0 over │      │                       │    └──────────────────┘
   │ stdio to kloc-    │      │                       │
   │ intelligence      │      │                       │
   │ subprocess        │      │                       │
   └──────────────────┘      └──────────────────────┘

                                  ┌──────────────────┐
                                  │ SKILLS (lazy)    │
                                  │                  │
                                  │ ./skills/        │
                                  │   skill-a/       │
                                  │     SKILL.md     │ ← L1: name+description
                                  │     references/  │   in system prompt
                                  │   skill-b/       │
                                  │     SKILL.md     │
                                  │                  │
                                  │ discover_skills( │
                                  │   "./skills")    │
                                  │ + generate_skills│
                                  │   _prompt(skills)│
                                  │                  │
                                  │ L2 disclosure:   │
                                  │ LLM calls        │
                                  │ file_read("...") │
                                  │ to load full body│
                                  │ on demand        │
                                  └──────────────────┘
```

### 3.1 Agent loop sequence (one user message → one RUN_FINISHED)

```
__main__.py        AgentFactory   StrandsAgent       Model        MCPClient      HookCb       Backend
    │                  │              │                │              │            │              │
    │── read hydration ┤              │                │              │            │              │
    │── create_agent ──►              │                │              │            │              │
    │                  ├─ build model ─┐               │              │            │              │
    │                  ├─ list_tools_sync(MCP) ────────►              │            │              │
    │                  │                │              │  list_tools  │            │              │
    │                  │                │              │◄─────────────┤            │              │
    │                  ├─ make summarizer Agent        │              │            │              │
    │                  ├─ add_callback(BeforeToolCall) ─────────────► registered   │              │
    │                  └─ return agent ─►               │              │            │              │
    │                                  │               │              │            │              │
    │── iter_inbound() ▼               │               │              │            │              │
    │── receive UserMessage            │               │              │            │              │
    │── ag_adapter.run(RunAgentInput) ►│               │              │            │              │
    │                                  ├─ RUN_STARTED  │              │            │              │
    │   ◄── emit(event) ── stdio JSONL │              │              │            │              │
    │                                  ├─ STATE_SNAPSHOT              │            │              │
    │                                  ├─ MESSAGES_SNAPSHOT           │            │              │
    │                                  ├─ TEXT_MESSAGE_START          │            │              │
    │                                  ├─ model.complete() ────────►  │            │              │
    │                                  ├─ TEXT_MESSAGE_CONTENT (delta)│            │              │
    │                                  ├─ … more deltas …             │            │              │
    │                                  ├─ TEXT_MESSAGE_END            │            │              │
    │                                  ├─ TOOL_CALL_START kloc_context│            │              │
    │                                  ├─ ── BeforeToolCallEvent ─────────────────►│              │
    │                                  │                              │            ├─POST hmac ──►│
    │                                  │                              │            │              │
    │                                  │                              │            │◄─{allow}──┤
    │                                  ├─ TOOL_CALL_ARGS deltas       │            │              │
    │                                  ├─ TOOL_CALL_END               │            │              │
    │                                  ├─ MCP call ───────────────────►            │              │
    │                                  │                              │            │              │
    │                                  ├─ TOOL_CALL_RESULT            │            │              │
    │                                  ├─ MESSAGES_SNAPSHOT           │            │              │
    │                                  ├─ TOOL_CALL_START summarizer  │            │              │
    │                                  ├─ ── BeforeToolCallEvent ─────────────────►│              │
    │                                  │   (sub-agent fires too!)     │            ├─POST hmac ──►│
    │                                  │                              │            │              │
    │                                  ├─ (sub-agent runs internally) │            │              │
    │                                  ├─ TOOL_CALL_RESULT            │            │              │
    │                                  ├─ TEXT_MESSAGE_START          │            │              │
    │                                  ├─ TEXT_MESSAGE_CONTENT*       │            │              │
    │                                  ├─ TEXT_MESSAGE_END            │            │              │
    │                                  └─ RUN_FINISHED                │            │              │
    │                                                                 │            │              │
    │── back to inbound loop                                          │            │              │
```

### 3.2 Configuration & process model summary

| Concern | Where it's set | Channel |
|---|---|---|
| `LLM_PROVIDER` (anthropic / openrouter / bedrock) | env at runner spawn | env var |
| `ANTHROPIC_API_KEY` | env at runner spawn (passed from backend) | env var |
| `KLOC_HYDRATION_PATH=/run/kloc/hydration.json` | mounted file | env var → file |
| `SESSION_ID`, `BACKEND_URL`, `MCP_URL` | env at spawn | env var |
| `HYDRATION` payload (prior_messages, skills_dir, etc.) | written by backend to a tempfile, bind-mounted read-only at the path above | mounted JSON file |
| Inbound user messages (mid-session) | `iter_inbound()` | long-poll `GET {BACKEND_URL}/internal/sessions/{id}/inbox` |
| Outbound AG-UI events | `emit(event)` | chunked HTTPS POST `{BACKEND_URL}/internal/sessions/{id}/events` (JSONL body) |
| Hook audit | `runner/hooks/audit.py` → `httpx.post` | HTTPS POST to backend webhook |
| Heartbeat (15 s) | runner background task | same channel as events |
| Shutdown | `container.stop(t=5)` → SIGKILL via Docker daemon | aiodocker |

### 3.3 Runner lifecycle & warm-idle eviction

Two distinct timers govern the runner's lifetime. Both are owned by the
backend's `RunnerRegistry`; the runner itself just runs and emits
heartbeats.

| Timer | Default | Trigger | What happens |
|---|---|---|---|
| **warm-idle** | `RUNNER_WARM_IDLE_S=60` | starts ticking on each `RUN_FINISHED` | container is **terminated and removed** after 60 s of silence between messages. Cancelled if a new user message arrives — the same container handles the follow-up. |
| **heartbeat-dead** | `RUNNER_HEARTBEAT_TIMEOUT_S=30` | no heartbeat seen for 30 s | container is assumed crashed → terminated; session marked `runner_state=crashed`; analyst sees "session interrupted, resume?" |

**Why warm-idle and not always-on / always-evict:**

- **Always-on** (keep the runner forever): wastes a container per analyst, blocks our 1 GiB / 2 vCPU budget, and runs up Anthropic token costs (idle MCP + skills prompts still loaded).
- **Always-evict** (terminate after every `RUN_FINISHED`): cold-spawn latency on every follow-up message (~2–5 s for image start + hydration). Awful UX for normal multi-turn conversation.
- **Warm-idle, 60 s window**: covers natural conversational rhythm — analyst reads the answer, types a follow-up, container is still warm. Walk-away → terminated quickly, no wasted resources.

**State machine.**

```
       ┌─────────────────────────────────────────────────────────────────┐
       │                                                                 │
       │   ┌──────────┐  spawn       ┌────────────┐                       │
       │   │  COLD    │ ────────────►│  STARTING  │                       │
       │   │  (no     │              │  (image    │                       │
       │   │container)│              │   pull,    │                       │
       │   └──────────┘              │  hydration)│                       │
       │       ▲                     └─────┬──────┘                       │
       │       │                           │ ready                        │
       │       │ terminate                 ▼                              │
       │       │                     ┌─────────────┐                      │
       │       │            ┌────────┤   RUNNING   │◄──────────┐          │
       │       │            │        │ (RUN_STARTED│           │          │
       │       │            │        │  ...        │           │ new user │
       │       │            │        │  RUN_FINISH │           │ message  │
       │       │            │        └─────┬───────┘           │ (cancel  │
       │       │            │              │                   │  warm    │
       │       │            │              │ RUN_FINISHED      │  timer)  │
       │       │            │              ▼                   │          │
       │       │            │        ┌─────────────┐           │          │
       │       │            │        │  WARM-IDLE  ├───────────┘          │
       │       │            │        │ (60 s timer │                      │
       │       │            │        │  ticking)   │                      │
       │       │            │        └─────┬───────┘                      │
       │       │            │              │ timer expired                │
       │       │            │              │                              │
       │       │            │ heartbeat    │                              │
       │       │            │ timeout      │ container.stop(t=5)          │
       │       │            ▼              ▼                              │
       │       │        ┌──────────────────────┐                          │
       │       └────────┤   TERMINATING        │                          │
       │                │  (graceful → kill)   │                          │
       │                └──────────────────────┘                          │
       │                                                                  │
       └──────────────────────────────────────────────────────────────────┘
```

**Implementation: per-session `asyncio.Task`, not a polling sweeper.**

The polling sweeper from brief 03 § 5 is replaced by an explicit
per-session task that holds an `asyncio.Event`:

```python
class WarmIdleManager:
    """One instance per running container, owned by RunnerRegistry."""
    def __init__(self, runner, handle, warm_idle_s: float):
        self._runner = runner
        self._handle = handle
        self._warm_idle_s = warm_idle_s
        self._activity = asyncio.Event()
        self._task: asyncio.Task | None = None

    def on_run_finished(self) -> None:
        # Start (or restart) the warm-idle countdown.
        if self._task and not self._task.done():
            self._task.cancel()
        self._activity.clear()
        self._task = asyncio.create_task(self._await_idle_then_kill())

    def on_user_message(self) -> None:
        # New activity — cancel the kill timer.
        self._activity.set()
        if self._task and not self._task.done():
            self._task.cancel()

    async def _await_idle_then_kill(self) -> None:
        try:
            await asyncio.wait_for(self._activity.wait(), timeout=self._warm_idle_s)
        except asyncio.TimeoutError:
            await self._runner.terminate(self._handle, graceful_timeout=5)
```

The heartbeat watcher is a separate task that does
`await asyncio.wait_for(self._heartbeat_seen.wait(), timeout=30)` and on
timeout marks the session `crashed` instead of `evicted`.

**Audit hooks for each transition** (write to `audit_log`):
`runner_spawned`, `run_started`, `run_finished`, `runner_warm_idle_evicted`,
`runner_heartbeat_lost`, `runner_terminated`.

### 3.4 Restore from memory on follow-up after warm-idle (same chat)

The whole point of warm-idle eviction is that we can kill freely — because
**all the analyst's context lives in Postgres**, never in the container.
When the next message arrives in the same `session_id`, we spawn a fresh
container and **rehydrate** so the conversation continues seamlessly. The
analyst sees no seam.

#### How it works

```
T=0    user types "What about the failed orders?"
        │
        ▼
       POST /v1/sessions/{id}/stream  (same session_id as yesterday)
        │
        ▼
     ┌───────────────────────────────────────────────────────────────┐
     │  Backend stream.py route:                                      │
     │                                                                │
     │  1. SessionRepo.get(id) → exists, status=open, runner=evicted  │
     │  2. MessageRepo.append(user_msg) + COMMIT                      │
     │  3. RunnerRegistry.get_or_spawn(session_id):                    │
     │       no live container for this session_id                     │
     │       → MessageRepo.list_for_session(id) → full message history │
     │       → SkillCatalogRepo.for_session(id)  → which skills enabled│
     │       → build HydrationPayload(                                 │
     │            session_id,                                          │
     │            system_prompt = base_prompt + skills_prompt,         │
     │            prior_messages = [...full history from DB...],       │
     │            state          = last STATE_SNAPSHOT from audit_log, │
     │            mcp_endpoints  = ["uv run kloc-intelligence ..."],   │
     │            skills_dir     = "/skills",                          │
     │            model_id       = settings.default_model_id)          │
     │       → DockerRunner.spawn(payload)                             │
     │  4. WarmIdleManager.on_user_message()  (no-op; first message)   │
     │  5. runner.send_user_message(handle, user_msg)                  │
     │  6. yield events as usual                                       │
     └───────────────────────────────────────────────────────────────┘
```

#### What the runner does with the hydration

```
runner/__main__.py:
   1. read /run/kloc/hydration.json  → HydrationPayload
   2. open MCPClient (kloc-intelligence stdio child)
   3. agent_factory.create_agent(payload):
        - model = AnthropicModel(...)
        - tools = MCP tools + summarizer_subagent
        - hooks attached
        - skills prompt injected into system_prompt
        - Agent constructed with NO session_manager  (Postgres is SoT)
   4. iter_inbound() → waits for first user message
   5. user message arrives via long-poll
   6. ag_adapter.run(RunAgentInput(
          thread_id = session_id,
          run_id    = new_uuid,
          messages  = prior_messages + [new user msg],   ← full context
          state     = state_from_hydration,
          tools     = [...]))
   7. ag_ui_strands.StrandsAgent.run() rebuilds the Strands Agent's
      internal `messages` list from RunAgentInput.messages BEFORE the
      first stream_async call — this is the "history reconciliation"
      behavior documented in ag-ui-protocol/ag-ui/integrations/
      aws-strands/ARCHITECTURE.md.
   8. LLM sees the full conversation and continues naturally.
```

**Key property**: the LLM has no way to tell whether this is turn 1 of a
fresh session, turn 7 of a hot container, or turn 7 of a session that was
killed-then-rehydrated. From the model's perspective, **history is history**.

#### What gets restored vs what does not

| Thing | Source on rehydrate | Notes |
|---|---|---|
| Conversation messages | `messages` table | full, ordered by `seq` |
| Tool call results | `messages.content_parts` (tool messages) | included in the history list |
| Shared UI state (`state`) | last `STATE_SNAPSHOT` in `audit_log` | applied as initial state on the new runner |
| Skills available | `skills/` directory (read-only mount) | re-loaded with `discover_skills()` |
| MCP connection | re-opened (stdio child of new container) | kloc-intelligence is stateless across our requests |
| **Mid-flight tool calls** | **not restored** | if the container was killed during a `TOOL_CALL_END → TOOL_CALL_RESULT` gap, the partial call is **lost**; surfaced to the analyst as "interrupted, retry?" (see brief 03 § 7) — warm-idle never triggers mid-run, so this only happens on actual crashes |

#### What's NOT in memory anywhere

Things we deliberately **don't** carry across runner deaths:

- The Strands `Agent` Python object (rebuilt from history)
- In-process Python dicts on the runner (Strands has none we care about)
- MCP `MCPClient` state (re-opened lazily)
- Per-run uuid `run_id` (new one each run; previous runs persist in DB)

This is what makes warm-idle eviction safe — the runner has nothing to
lose. **Postgres + MinIO + `skills/` mount is the entire durable surface.**

#### Cold-start cost (acceptable for warm-idle resume)

Spawn → ready latency on a warm Docker daemon:

| Stage | ~ time |
|---|---|
| `aiodocker.containers.create()` + `start()` | 200–400 ms |
| Container boots Python + imports | 300–600 ms |
| Read hydration file, build Agent | 50–100 ms |
| Open MCPClient (stdio child + `list_tools_sync`) | 200–500 ms (kloc-intelligence's lazy Neo4j connection) |
| **Total** | **~1–2 s** before the first model token streams |

This is the cost the analyst pays on a follow-up after the warm-idle
window has closed. Within the 60-second window, it's zero — same
container handles the next message instantly.

If we ever need to make cold-start invisible (e.g. for a very long-tail
analyst pattern), the options in priority order would be:
1. Lengthen `RUNNER_WARM_IDLE_S` (cheapest)
2. Image-prewarm: keep one "spare" idle container ready, hot-swap session_id (more complex)
3. Process-pool inside one container handling N sessions (complicates isolation; probably wrong choice for analyst workloads)

We do not implement (2) or (3) for PoC.

---

## Cross-reference

- The components in (1) map to research briefs: UI → `02`, Backend → `02`+`04`, Runner → `03`, MCP & LLM links → `01`.
- The internal modules in (2) map to `investigation.md` §3 (module layout).
- The agent organization in (3) is the runtime view of `investigation.md` §6 (vertical PoC slice).
