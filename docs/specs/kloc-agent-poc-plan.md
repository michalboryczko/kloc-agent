# Plan: kloc-agent (PoC)

> Implementation plan for `kloc-agent` PoC. The feature spec
> ([`kloc-agent-poc.md`](kloc-agent-poc.md)) lists 26 acceptance criteria
> across 14 QA scenarios. This plan turns those into a file manifest,
> ownership table, four interface contracts, and a phased task list for
> three developer streams + QA. The authoritative source for design
> decisions is [`../investigation.md`](../investigation.md) §2.1 (locked
> stack) and [`../architecture.md`](../architecture.md) §3.3–§3.4
> (warm-idle + rehydrate). This plan does not duplicate them — it
> references them.
>
> **Date:** 2026-05-14. **Status:** v1, pending team-lead review.
> **Stand-down rule:** after v1 is accepted, the architect does not
> iterate. Plan amendments only on real developer-flagged ambiguities.

---

## Overview

### Problem statement

Analysts cannot consume `kloc-intelligence` today: it ships as MCP /
CLI usable through Claude Code on one engineer's workstation, not as
a hosted service for many analysts. `kloc-agent` adds the missing
layer — a hosted research-agent service that takes natural-language
questions in a web chat, runs a Strands `Agent` per session inside an
ephemeral Docker container, calls `kloc-intelligence` MCP tools, and
streams sourced answers back over SSE.

### Acceptance criteria

26 ACs are listed in [`kloc-agent-poc.md`](kloc-agent-poc.md)
§ Acceptance Criteria; they are grouped into 14 QA scenarios
(QA1–QA14). This plan maps every AC to one or more phased tasks and
one or more test cases.

### Constraints (recap from spec § Critical Constraints)

1. Single chat = single `session_id`. Runner is warm-idle 60 s, then
   killed; next message respawns + rehydrates from Postgres. No seam.
2. Backend never runs the agent loop or calls the model — per-session
   Docker containers do.
3. Strands hooks are in-process only; webhook is `httpx` wrapped
   inside the hook callback.
4. Strands silently defaults to Bedrock — always pass
   `AnthropicModel(...)` explicitly via `LLM_PROVIDER` env switch.
5. `strands_agentskills` is not on PyPI — install from git pinned to
   a specific commit hash.

Also load-bearing: **`ag-ui-protocol == 0.1.18` (Python) ↔
`@ag-ui/client == 0.0.42` (JS)** — pair-pin enforced as AC26.

---

## Codebase Summary

`kloc-agent` is **greenfield** under
`/Users/michal/dev/ai/kloc/kloc-agent/`. Only `poc.md` and the
`docs/` tree exist today; no `src/`, no `runner/`, no `frontend/`,
no `tests/`. The plan is therefore a creation plan, not a
modification plan.

The four reference repos catalogued in
[`../research/05-reference-projects.md`](../research/05-reference-projects.md)
contribute the following patterns to `kloc-agent`. We **copy** the
exact patterns marked below; **adapt** others; **skip** all
AgentCore-specific paths.

| From | Pattern | Verdict |
|---|---|---|
| Repo 2 | `src/streaming/execution_registry.py` shape + cursor-replay | Copy |
| Repo 2 | `src/streaming/agui_event_formatter.py` Strands→AG-UI map | Copy |
| Repo 2 | `src/api/health.py`, `src/api/stop.py` shapes | Copy |
| Repo 2 | `src/agent/hooks/utils.py:resolve_tool_call` (7 lines, unwraps `skill_executor`) | Copy |
| Repo 2 | `pytest.ini` markers + `tests/{unit,integration,e2e}/` + `tests/fixtures/` | Copy |
| Repo 2 | `frontend/src/utils/sseParser.ts` | Copy (fallback path, not the CopilotKit hot path) |
| Repo 2 | `Dockerfile` `opentelemetry-instrument` CMD wrapping | Copy |
| Repo 3 | File-per-route in `src/api/<concern>.py` | Copy |
| Repo 3 | Multi-provider model factory env switch (`LLM_PROVIDER=...`) | Copy |
| Repo 4 | `frontend/src/app/api/copilotkit/route.ts` + `agent-proxy/route.ts` (the AG-UI envelope glue) | Copy |
| Repo 4 | `<CopilotKit runtimeUrl="..." agent="kloc_agent">` provider | Copy |
| Repo 4 | uv + `pyproject.toml` build shape | Copy |

The Strands SDK fundamentals confirmed via `Skill(strands)` →
`Skill(strands-core-concepts)`:

- **Sessions resource**: don't pass `session_manager=` to `Agent()`.
  We rehydrate via `RunAgentInput.messages`; the
  `ag_ui_strands.StrandsAgent` adapter rebuilds Strands' internal
  history on every `agent.run(input)` call. Matches investigation.md
  §2.1.
- **Hooks resource**: `BeforeToolCallEvent.cancel_tool` is the
  mutable mechanism for policy-deny — assigning a string aborts the
  tool with that message. Used in `runner/hooks/audit.py`.
- **Streaming resource**: `agent.stream_async()` is the async-
  generator entry point. `ag_ui_strands.StrandsAgent.run` wraps it
  and emits AG-UI events directly; the FastAPI side just SSE-encodes
  via `ag_ui.encoder.EventEncoder`.

---

## Technical Approach

The locked stack is in
[`../investigation.md`](../investigation.md) §2.1 (a 30-row table
that resolves every "to choose" item in `poc.md`). The non-obvious
choices and their rationale:

- **Docker per session, one mode** (Track D, dev-2). The
  `Runner` Protocol is kept as a one-line seam for test fakes but
  there is no second concrete impl. Production parity from day one.
- **JSONL wire format for runner↔backend** — outbound chunked HTTPS
  POST to `/internal/sessions/{id}/events`; inbound long-poll
  `GET /internal/sessions/{id}/inbox`. Hooks go on a separate HTTPS
  POST `/v1/webhooks/runners/{id}/events` with HMAC-SHA256.
- **Hydration via mounted JSON file** at
  `KLOC_HYDRATION_PATH=/run/kloc/hydration.json`. Backend writes
  `/tmp/hydration-<rid>.json` and bind-mounts it read-only at spawn.
- **Warm-idle eviction** with two timers: `RUNNER_WARM_IDLE_S=60` from
  `RUN_FINISHED` (cancellable on new user message);
  `RUNNER_HEARTBEAT_TIMEOUT_S=30` for crash detection. Both backend-
  owned (`WarmIdleManager`, heartbeat watcher).
- **Same-chat rehydrate** is safe because Postgres + MinIO +
  `./skills/` is the entire durable surface; the runner has nothing
  to lose.
- **Persistence ordering invariants** (architecture.md §2.1A):
  1. Persist user `Message` row → commit → forward to runner.
  2. Batch text deltas (256-char / 250-ms) via
     `UPDATE messages SET content = content || $1 ...`.
  3. Persist `audit_log` row **before** responding to hook webhook.
- **CopilotKit on top of AG-UI** for the frontend. We adopt
  CopilotKit (Repo 4 pattern) because `useCoAgent`, `useCopilotAction`,
  and ready chat shells save real code; the Repo-2-style raw AG-UI
  parser is kept as a fallback in `frontend/src/utils/sseParser.ts`
  but is not on the critical path.

---

## File Manifest

Every file that has to exist for the PoC to run. All paths relative
to `/Users/michal/dev/ai/kloc/kloc-agent/`. CREATE for all — this is
greenfield.

### Repo root

| Action | File | Owner |
|---|---|---|
| CREATE | `pyproject.toml` (uv-managed) | dev-1 |
| CREATE | `uv.lock` | dev-1 (auto) |
| CREATE | `.env.example` | dev-1 |
| CREATE | `Dockerfile` (backend image) | dev-1 |
| CREATE | `docker-compose.yml` | dev-1 |
| CREATE | `docker-compose.dev.yml` (overrides) | dev-1 |
| CREATE | `alembic.ini` | dev-1 |
| CREATE | `Makefile` (targets: `build-runner`, `compose-up`, `test`) | dev-1 |
| CREATE | `README.md` (links to docs, quick-start) | dev-1 |

### `migrations/`

| Action | File | Owner |
|---|---|---|
| CREATE | `migrations/env.py` (async Alembic) | dev-1 |
| CREATE | `migrations/script.py.mako` | dev-1 |
| CREATE | `migrations/versions/2026_05_14_0001_init.py` | dev-1 |

### `src/` (backend)

| Action | File | Owner |
|---|---|---|
| CREATE | `src/__init__.py` | dev-1 |
| CREATE | `src/main.py` (FastAPI app + lifespan) | dev-1 |
| CREATE | `src/settings.py` (pydantic-settings) | dev-1 |
| CREATE | `src/api/__init__.py` | dev-1 |
| CREATE | `src/api/sessions.py` (lifecycle REST) | dev-1 |
| CREATE | `src/api/stream.py` (SSE POST + GET resume) | **dev-2** |
| CREATE | `src/api/internal.py` (runner JSONL ingress + inbox long-poll) | dev-1 |
| CREATE | `src/api/webhooks.py` (HMAC hook receiver) | dev-1 |
| CREATE | `src/api/artifacts.py` (presigned URL) | dev-1 |
| CREATE | `src/api/health.py` (`/healthz`, `/readyz`) | dev-1 |
| CREATE | `src/api/stop.py` (run cancel) | dev-1 |
| CREATE | `src/db/__init__.py` | dev-1 |
| CREATE | `src/db/base.py` (DeclarativeBase + naming convention) | dev-1 |
| CREATE | `src/db/engine.py` (async engine + sessionmaker) | dev-1 |
| CREATE | `src/db/deps.py` (`get_session()`) | dev-1 |
| CREATE | `src/db/models.py` (Session, Message, AuditLog, ArtifactMetadata) | dev-1 |
| CREATE | `src/repos/__init__.py` | dev-1 |
| CREATE | `src/repos/sessions.py` | dev-1 |
| CREATE | `src/repos/messages.py` (incl. batched debounce-append SQL) | dev-1 |
| CREATE | `src/repos/audit.py` | dev-1 |
| CREATE | `src/repos/artifacts.py` | dev-1 |
| CREATE | `src/storage/__init__.py` | dev-1 |
| CREATE | `src/storage/s3.py` (aioboto3 helpers) | dev-1 |
| CREATE | `src/hooks_audit/__init__.py` | dev-1 |
| CREATE | `src/hooks_audit/verify_hmac.py` | dev-1 |
| CREATE | `src/hooks_audit/policy.py` (PoC: allow-all stub) | dev-1 |
| CREATE | `src/streaming/__init__.py` | **dev-2** |
| CREATE | `src/streaming/execution_registry.py` (lift from Repo 2) | **dev-2** |
| CREATE | `src/streaming/agui_event_formatter.py` (lift from Repo 2) | **dev-2** |
| CREATE | `src/streaming/event_bus.py` (in-proc pub/sub) | **dev-2** |
| CREATE | `src/streaming/sse.py` (`EventEncoder` wrapper + StreamingResponse) | **dev-2** |
| CREATE | `src/streaming/debounce.py` (256-char / 250-ms buffered append, calls `MessageRepo.append_delta()`) | **dev-2** |
| CREATE | `src/runner_mgmt/__init__.py` | **dev-2** |
| CREATE | `src/runner_mgmt/protocol.py` (`Runner` Protocol; re-exports `HydrationPayload` from `src/db/models.py` for runner-side imports) | **dev-2** |
| CREATE | `src/runner_mgmt/docker_runner.py` (aiodocker impl) | **dev-2** |
| CREATE | `src/runner_mgmt/registry.py` (`RunnerRegistry` — stub on day 1) | **dev-2** |
| CREATE | `src/runner_mgmt/warm_idle.py` (`WarmIdleManager`) | **dev-2** |
| CREATE | `src/runner_mgmt/heartbeat.py` (heartbeat watcher) | **dev-2** |
| CREATE | `src/runner_mgmt/hydrate.py` (write tempfile + bind-mount config) | **dev-2** |
| CREATE | `src/runner_mgmt/sweeper.py` (boot-time orphan-container sweep) | **dev-2** |
| CREATE | `src/tools/__init__.py` (sparse; placeholder) | **dev-2** |

### `runner/` (code inside each Docker container)

| Action | File | Owner |
|---|---|---|
| CREATE | `runner/__init__.py` | **dev-2** |
| CREATE | `runner/__main__.py` (entrypoint; reads hydration, runs inbound loop) | **dev-2** |
| CREATE | `runner/Dockerfile` | **dev-2** |
| CREATE | `runner/agent_factory.py` (`create_agent(payload)`) | **dev-2** |
| CREATE | `runner/model_factory.py` (`LLM_PROVIDER` switch) | **dev-2** |
| CREATE | `runner/mcp_clients.py` (stdio `MCPClient` for kloc-intelligence) | **dev-2** |
| CREATE | `runner/channel.py` (outbound chunked POST + inbound long-poll + heartbeat) | **dev-2** |
| CREATE | `runner/hooks/__init__.py` | **dev-2** |
| CREATE | `runner/hooks/audit.py` (`BeforeToolCallEvent` → webhook) | **dev-2** |
| CREATE | `runner/hooks/utils.py` (`resolve_tool_call`, lift from Repo 2) | **dev-2** |

### `skills/` (repo-root, bind-mounted into runners)

| Action | File | Owner |
|---|---|---|
| CREATE | `skills/summarize-callgraph/SKILL.md` (one demo skill for AC8/AC9) | **dev-2** |

### `frontend/` (Next.js 16 + CopilotKit 1.52.1 + AG-UI 0.0.42)

| Action | File | Owner |
|---|---|---|
| CREATE | `frontend/package.json` | **dev-3** |
| CREATE | `frontend/pnpm-lock.yaml` | **dev-3** (auto) |
| CREATE | `frontend/next.config.ts` | **dev-3** |
| CREATE | `frontend/tsconfig.json` | **dev-3** |
| CREATE | `frontend/postcss.config.mjs` | **dev-3** |
| CREATE | `frontend/eslint.config.mjs` | **dev-3** |
| CREATE | `frontend/Dockerfile` | **dev-3** |
| CREATE | `frontend/.env.local.example` | **dev-3** |
| CREATE | `frontend/src/app/layout.tsx` (`<CopilotKit ...>`) | **dev-3** |
| CREATE | `frontend/src/app/page.tsx` (`<CopilotSidebar>`, `useCoAgent`) | **dev-3** |
| CREATE | `frontend/src/app/globals.css` | **dev-3** |
| CREATE | `frontend/src/app/api/copilotkit/route.ts` (CopilotRuntime + HttpAgent) | **dev-3** |
| CREATE | `frontend/src/app/api/agent-proxy/route.ts` (AG-UI envelope + SSE proxy) | **dev-3** |
| CREATE | `frontend/src/lib/api.ts` (session lifecycle REST) | **dev-3** |
| CREATE | `frontend/src/utils/sseParser.ts` (lift from Repo 2; fallback) | **dev-3** |
| CREATE | `frontend/src/components/ToolCallCard.tsx` (basic generative UI render) | **dev-3** |

### `tests/`

| Action | File | Owner |
|---|---|---|
| CREATE | `tests/__init__.py` | qa |
| CREATE | `tests/pytest.ini` (asyncio_mode=auto, markers) | qa |
| CREATE | `tests/conftest.py` (common fixtures: `db`, `s3`, `client`, `runner_mock`) | qa |
| CREATE | `tests/fixtures/__init__.py` | qa |
| CREATE | `tests/fixtures/mock_model_provider.py` | qa |
| CREATE | `tests/fixtures/mock_session_manager.py` | qa |
| CREATE | `tests/fixtures/mock_tools.py` | qa |
| CREATE | `tests/fixtures/mock_runner.py` (in-proc fake satisfying `Runner` Protocol) | qa |
| CREATE | `tests/unit/test_repos.py` | dev-1 (own code) |
| CREATE | `tests/unit/test_hmac.py` | dev-1 |
| CREATE | `tests/unit/test_debounce.py` | dev-2 |
| CREATE | `tests/unit/test_warm_idle.py` | dev-2 |
| CREATE | `tests/unit/test_agui_formatter.py` | dev-2 |
| CREATE | `tests/integration/test_docker_runner.py` (spawn + JSONL round-trip + eviction) | qa |
| CREATE | `tests/integration/test_sse_encoder.py` | qa |
| CREATE | `tests/integration/test_resume_after_disconnect.py` (QA9) | qa |
| CREATE | `tests/integration/test_rehydrate.py` (QA8) | qa |
| CREATE | `tests/integration/test_hook_deny.py` (QA10) | qa |
| CREATE | `tests/integration/test_concurrent_sessions.py` (QA12) | qa |
| CREATE | `tests/integration/test_orphan_sweep.py` (QA11/AC25) | qa |
| CREATE | `tests/integration/test_dependency_pinning.py` (QA14/AC26) | qa |
| CREATE | `tests/e2e/sse_client.py` (lift from Repo 2) | qa |
| CREATE | `tests/e2e/test_vertical_slice.py` (full PoC; pytest -m e2e) | qa |

### Total CREATE: 77 files across 8 directory roots.

---

## File Ownership Table

**Critical artifact.** Every file in the manifest above has exactly
one owner. No two developers edit the same file. Conflicts are
resolved by PR-style `SendMessage` change-requests to the owner.

| Owner | Tracks | Files | Rationale |
|---|---|---|---|
| **dev-1** (Backend foundation) | A, B, C minus stream.py | All under `src/api/{sessions,internal,webhooks,artifacts,health,stop}.py`, all of `src/db/*`, `src/repos/*`, `src/storage/s3.py`, `src/hooks_audit/*`, `src/settings.py`, `src/main.py`. All under `migrations/*`. All repo-root infra: `pyproject.toml`, `uv.lock`, `.env.example`, `Dockerfile`, `docker-compose.yml`, `docker-compose.dev.yml`, `alembic.ini`, `Makefile`, `README.md`. Unit tests for dev-1's own code (`tests/unit/test_repos.py`, `tests/unit/test_hmac.py`). | Owns persistence + HTTP surface + compose stack. |
| **dev-2** (Runner + skills + streaming) | D, E, half of C | `src/api/stream.py`. All under `src/runner_mgmt/*`, `src/streaming/*`, `src/tools/*`. All under `runner/*` (incl. `runner/Dockerfile`). `skills/summarize-callgraph/SKILL.md`. Unit tests for dev-2's own code. | Owns agent runtime + the SSE generator that pumps runner events out. |
| **dev-3** (Frontend + obs) | F, H | All under `frontend/*` (incl. `frontend/Dockerfile`). | Owns the entire Next.js tree + OTel doc/env entries (sent as change-requests to dev-1 / dev-2). |
| **qa** | G | All under `tests/` except `tests/unit/test_*.py` files for dev-owned code. Owns layout, conftest, fixtures, integration + e2e. | Owns regression coverage for the 14 QA scenarios. |

### Contested files & change-request convention

| File | Owner | Contributors | Convention |
|---|---|---|---|
| `src/main.py` | dev-1 | dev-2 (runner_registry init, orphan sweeper, warm-idle managers in lifespan) | dev-2 sends one bundled change-request via `SendMessage` listing the exact lines to add. Dev-1 applies. |
| `pyproject.toml` | dev-1 | dev-2 (Strands deps), dev-3 (NONE — frontend uses package.json) | dev-2 sends **one** bundled PR-style change-request before Track D kickoff listing: `strands-agents==1.39.0`, `ag-ui-protocol==0.1.18`, `ag_ui_strands==0.1.8`, `strands_agentskills @ git+https://github.com/aws-samples/sample-strands-agents-agentskills@<sha>`, `aiodocker>=0.26`, `mcp`, `httpx`. |
| `.env.example` | dev-1 | dev-2 (runner env), dev-3 (frontend + OTel env) | Each sends a single bundled change-request listing all their entries. |
| `docker-compose.yml` | dev-1 | dev-3 (frontend service) | Dev-3 sends a single change-request with the frontend service block. dev-2 does **not** add a runner service (runners are spawned by backend via aiodocker, not declared in compose). |
| `Dockerfile` (backend) | dev-1 | dev-3 (`opentelemetry-instrument` CMD wrap) | Dev-3 sends a single change-request when Track H starts. |
| `runner/Dockerfile` | dev-2 | dev-3 (`opentelemetry-instrument` CMD wrap) | Dev-3 sends a single change-request when Track H starts. |

### Cross-stream read-only imports

| Reader | Imports | Writer | Contract |
|---|---|---|---|
| `runner/agent_factory.py` (dev-2) | `src/db/models.py` typed payloads only | dev-1 | dev-2 imports **type definitions** (HydrationPayload Pydantic shape) but **not** service code (`repos/*`, `storage/s3.py`). Documented in Interface Contract D below. |
| `src/api/stream.py` (dev-2) | `src/repos/messages.py`, `src/repos/audit.py` | dev-1 | dev-2 calls dev-1's repos via `Depends(get_session)`. dev-1 must keep the repo signatures stable; signature changes require change-request. |
| `src/streaming/debounce.py` (dev-2) | `src/repos/messages.py:append_delta()` | dev-1 | The 256-char / 250-ms debouncer is in dev-2's streaming module; the actual UPDATE SQL is in dev-1's `MessageRepo.append_delta()`. Contract: dev-1 exposes `async def append_delta(session, message_id, delta_str) -> None` doing the server-side `content = content \|\| $1` UPDATE. (AC4c.) |

### `src/skill_loader/*` is intentionally absent

Per Phase 1 boundary resolution (#4): the optional Repo-2-style
wrapper layer is **skipped** for PoC. `runner/agent_factory.py`
imports `strands_agentskills` (`discover_skills`,
`generate_skills_prompt`) directly. Revisit in v2 if we adopt the
`skill_executor` pattern.

### `runner/skills/` is intentionally absent

Per Phase 1 boundary resolution (#6): the skills live at repo-root
`./skills/<name>/SKILL.md` and are bind-mounted read-only into the
runner container at `/skills`. `runner/` does not have its own
skills subdirectory — the directory hint in
[`../investigation.md`](../investigation.md) §3 is misleading.

---

## Interface Contracts

Four boundaries, locked in this plan. Any change to these requires
a SendMessage to the owning stream.

### Contract A — Backend ↔ Frontend (REST + SSE)

**Owner:** dev-1 for `/v1/sessions/*` + `/v1/webhooks/*` +
`/v1/artifacts/*`; dev-2 for `/v1/sessions/{id}/stream`.

Routes (full prose: spec § Acceptance Criteria, `investigation.md` §5):

```
# Session lifecycle (JSON)
POST   /v1/sessions                                  → 201 {session_id, created_at}
GET    /v1/sessions/{id}                             → 200 {id, status, runner_state, message_count, ...}
GET    /v1/sessions/{id}/messages?after=cursor&limit=100  → 200 {messages: [...], next_cursor, has_more}
POST   /v1/sessions/{id}/messages                    → 202 {run_id, stream_url}
POST   /v1/sessions/{id}/close                       → 204
POST   /v1/sessions/{id}/runs/{run_id}/cancel        → 204

# Streaming (SSE — text/event-stream)
GET    /v1/sessions/{id}/stream?run_id=...&last_event_id=...      Content-Type: text/event-stream
POST   /v1/sessions/{id}/stream                      body: RunAgentInput   Content-Type: text/event-stream

# Artifacts
GET    /v1/artifacts/{id}                            → 302 (presigned MinIO URL)
```

SSE wire format (every event):

```
data: <ag_ui.encoder.EventEncoder.encode(event)>\n\n
```

The 33 AG-UI event types are enumerated in
[`../research/02-backend-agui.md`](../research/02-backend-agui.md) §2.
Pair-pin baseline: `ag-ui-protocol == 0.1.18` (Python) ↔
`@ag-ui/client == 0.0.42` (JS). AC26 is the build-time test.

**Persistence ordering invariants** (binding on dev-1 and dev-2):

1. **User message**: `MessageRepo.append(user_msg)` + commit *before*
   `runner.send_user_message(handle, ...)`. (AC4a.)
2. **Text deltas**: every `TEXT_MESSAGE_CONTENT` flows through
   `src/streaming/debounce.py`, which calls
   `MessageRepo.append_delta(session, message_id, delta_str)`
   with 256-char / 250-ms buffering. Flush on `TEXT_MESSAGE_END` →
   set `messages.finalized_at = now()`. (AC4c.)
3. **Tool calls**: persist `AssistantMessage(tool_calls=[...])` at
   `TOOL_CALL_END`; persist `ToolMessage` at `TOOL_CALL_RESULT`.
4. **State**: persist row in `audit_log` at every `STATE_SNAPSHOT`;
   apply `STATE_DELTA` to in-memory state + persist deltas.

### Contract B — Backend ↔ Runner (JSONL over HTTP)

**Owner:** dev-1 owns the backend half (`src/api/internal.py`);
dev-2 owns the runner half (`runner/channel.py`).

Two transports:

```
# Outbound (runner → backend)
POST   /internal/sessions/{id}/events                Transfer-Encoding: chunked
       body: JSONL stream of AG-UI events + runner-internal events
       Auth: localhost-only (compose bridge), no public auth in PoC

# Inbound (backend → runner)
GET    /internal/sessions/{id}/inbox                 long-poll (≤ 30 s)
       returns: { type: "user_message", message_id, content_parts } | { type: "shutdown" } | (timeout: 204)
```

JSONL line shape (outbound, every line is one JSON object):

```
{"type": "RUN_STARTED", "threadId": "...", "runId": "...", ...}
{"type": "TEXT_MESSAGE_CONTENT", "messageId": "...", "delta": "..."}
{"type": "TOOL_CALL_START", "toolCallId": "...", ...}
...
{"type": "RUN_FINISHED", "threadId": "...", "runId": "...", "outcome": {"type": "success"}}
{"type": "heartbeat", "session_id": "...", "ts": "...", "busy": true|false}
```

**Heartbeat cadence**: runner emits every **15 s**, busy or not.
Backend's heartbeat watcher times out at **30 s** of silence →
crashed. (Investigation.md §2.1.)

**Routing**: backend reads JSONL frames, pushes them into the
`ExecutionRegistry` keyed by `(session_id, run_id)`. The SSE
generator in `src/api/stream.py` consumes from the registry.

### Contract C — Runner → Backend (HMAC Hook Webhook)

**Owner:** dev-1 owns the receiver (`src/api/webhooks.py`,
`src/hooks_audit/*`); dev-2 owns the sender (`runner/hooks/audit.py`).

```
POST   /v1/webhooks/runners/{runner_id}/events
Headers:
  Authorization: HMAC <base64(HMAC_SHA256(secret, f"{timestamp}.{raw_body}"))>
  X-Kloc-Hook-Event: BeforeToolCall | AfterToolCall | ...
  X-Kloc-Hook-Ts:    <unix_ms>
  Content-Type:      application/json
Body:
  {
    "event": "BeforeToolCall",
    "runner_id": "runner_01HM...",
    "session_id": "ses_01HM...",
    "run_id": "run_01HM...",
    "timestamp": 1747200001234,
    "payload": { "tool_call_id": "...", "tool_name": "...", "args": {...} }
  }
Response (sync for Before*; fire-and-forget for After*):
  202 { "decision": "allow" | "deny", "reason"?: "<string>" }
```

**Behavioural contract** (binding on `runner/hooks/audit.py`):

1. **Sync semantics** for `BeforeToolCallEvent`: callback blocks the
   tool call until the backend responds OR 2 s elapses.
2. **Deadline**: 2 s timeout on `httpx.post(...)`. On timeout:
   - Set `event.cancel_tool = "policy_deadline_exceeded"`.
   - Emit a `CustomEvent(name="HookBackpressure", value={"event": "BeforeToolCall", "tool": ..., "reason": "deadline"})` to the AG-UI stream. (AC12.)
3. **Deny path**: if response is `{decision: "deny", reason}`, set
   `event.cancel_tool = reason`. The Strands SDK aborts the tool
   with that string; analyst sees a `TOOL_CALL_RESULT` carrying
   the denial. (AC19.)
4. **Replay protection**: backend rejects if
   `|now - X-Kloc-Hook-Ts| > 60 s`. (AC11.)
5. **Persist before respond**: backend writes the `audit_log` row
   in the same DB transaction as the policy decision, commits, then
   responds. (AC10.)
6. **`resolve_tool_call(event)`** (in `runner/hooks/utils.py`): unwraps
   `skill_executor` wrapper calls so the hook sees the real underlying
   tool name. Lifted verbatim from Repo 2.
7. **`AfterToolCall` (fire-and-forget)**: bounded async queue of 256.
   Drop heartbeats first when full; never drop `Before*`. Emit one
   `CustomEvent(name="HookBackpressure", value={...})` on first drop
   to keep the audit chain.

**Test-deny env (C2 from QA):** `Policy.decide()` consults
`KLOC_DENY_TOOLS` env var (comma-separated tool names, e.g.
`KLOC_DENY_TOOLS=kloc_search,kloc_context`). If the request's
`payload.tool_name` matches any entry, return
`{decision: "deny", reason: "test-deny:<tool>"}`. This is the
canonical fixture for AC19 / QA scenario 10 — e2e tests set this env
on the backend container before exercising the deny path; default
unset means allow-all (PoC behavior). Declared in `src/settings.py`
(Phase 1.A2) and read inside `src/hooks_audit/policy.py` (Phase 1.C-1.4).

**HMAC helper exports (C6 from QA):**
`src/hooks_audit/verify_hmac.py` MUST export two functions:

```python
def verify_hmac_signature(body: bytes, ts: int, sig_b64: str, secret: str) -> bool: ...
def sign_for_test(body: bytes, ts: int, secret: str) -> str: ...
```

`sign_for_test` is required so QA's e2e tests can produce validly-
signed webhook payloads without re-implementing the algorithm.
Both functions share the same canonicalisation: `f"{ts}.{body}"` →
HMAC-SHA256 → base64. Phase 1.C-1.3 produces both.

### Contract D — Hydration JSON Schema (bind-mounted file)

**Owners (split):**
- dev-1 owns the Pydantic schema (`src/db/models.py:HydrationPayload`, `:McpStdioEndpoint`) — the single source of truth that defines field names, types, and validation.
- dev-2 owns the tempfile writer (`src/runner_mgmt/hydrate.py`) and the runner-side reader (`runner/__main__.py`). dev-2 imports the schema as a type-only cross-stream import per the "Cross-stream read-only imports" table.
- Schema changes require dev-2 to send a SendMessage change-request to dev-1; dev-2 may not alter `src/db/models.py` directly.

Path: `/run/kloc/hydration.json` (bind-mounted read-only from
`/tmp/hydration-<rid>.json` on the host). Schema (Pydantic v2 in
`src/db/models.py` so both ends can share — dev-2 imports type
definitions only, not service code):

```python
class HydrationPayload(BaseModel):
    session_id: str                       # uuid string
    run_id: str                           # uuid string (new for each spawn)
    runner_id: str                        # uuid string (unique per container)
    runner_secret: str                    # base64 HMAC secret for this runner

    # Agent construction
    system_prompt: str                    # base prompt + skills prompt prefix
    model_id: str                         # e.g. "claude-sonnet-4-6"
    llm_provider: Literal["anthropic", "openrouter", "bedrock"]
    prior_messages: list[Message]         # AG-UI Message shape; ag-ui-protocol 0.1.18
    state: dict[str, Any]                 # last STATE_SNAPSHOT (may be empty)

    # MCP
    mcp_endpoints: list[McpStdioEndpoint] # for PoC: one stdio spec for kloc-intelligence

    # Skills
    skills_dir: str = "/skills"           # mount path

    # Channel
    backend_url: str                      # e.g. "http://backend:8000"
    inbox_poll_timeout_s: int = 25        # long-poll budget (< 30 s heartbeat budget)
    heartbeat_interval_s: int = 15

class McpStdioEndpoint(BaseModel):
    command: str                          # "uv"
    args: list[str]                       # ["run", "kloc-intelligence", "mcp-server", "--database", "demo"]
    env: dict[str, str] = {}              # passed through to subprocess
```

**Contract notes** (binding on both ends):

- The runner **MUST** treat the file as read-only and crash if
  it cannot parse (no in-place defaults — every field must be present
  exactly as written by the backend).
- The backend **MUST** delete `/tmp/hydration-<rid>.json` in
  `DockerRunner.terminate(...)` after `container.wait()` returns.
- `prior_messages` is the **full conversation** from Postgres ordered
  by `messages.seq`. Token-window summarization is **out of scope**
  for PoC (investigation.md §2.2); the runner trusts what it gets.
- `runner_secret` is a per-runner shared secret used for HMAC. Backend
  stores it in the `RunnerHandle` only; never persists it. On
  container termination it goes out of scope.

---

## Audit event vocabulary (C3 from QA — LOCKED)

`audit_log.event_type` is `text` (not enum) but the in-app `Literal`
is locked. Every emitter MUST use exactly one of these names; QA's
scenario assertions match string-equality.

| Event name | Emitter | When |
|---|---|---|
| `session_opened` | `src/api/sessions.py` | `POST /v1/sessions` succeeds. (AC1.) |
| `session_closed` | `src/api/sessions.py` | `POST /v1/sessions/{id}/close` succeeds. (AC3.) |
| `message_persisted` | `src/repos/messages.py:finalize` | `TEXT_MESSAGE_END` flushes + finalizes. (AC4c.) |
| `stream_orphaned` | boot recovery (Phase 1.B13) | Startup scan finds `finalized_at IS NULL`. (AC24.) |
| `tool_call.started` | `src/api/webhooks.py` | `BeforeToolCall` audit row (allow path). (AC10.) |
| `tool_call.completed` | `src/api/webhooks.py` | `AfterToolCall` fire-and-forget row. |
| `tool_call.denied` | `src/api/webhooks.py` | `BeforeToolCall` audit row (deny path). (AC19.) |
| `tool_call.crashed` | `src/runner_mgmt/heartbeat.py` | Mid-flight tool call when runner crashes. (AC20, AC21.) |
| `runner_spawned` | `src/runner_mgmt/registry.py:get_or_spawn` | Fresh container started. |
| `runner_warm_idle_evicted` | `src/runner_mgmt/warm_idle.py` | 60 s timer expired → terminate. (AC13.) |
| `runner_heartbeat_lost` | `src/runner_mgmt/heartbeat.py` | 30 s no heartbeat → terminate. (AC20.) |
| `artifact_registered` | `src/api/webhooks.py` artifact webhook OR `src/repos/artifacts.py:register` | Successful artifact metadata insert. (AC23.) |

**Single source of truth**: `src/db/models.py` exposes
`AuditEventType = Literal["session_opened", "session_closed", ...]`
(the 12 names above). Every emitter type-hints against this Literal.
Adding a new event name is a deliberate plan amendment, not an ad-hoc
string. QA scenario 7 (warm-idle) asserts `runner_warm_idle_evicted`;
scenario 11 (crash recovery) asserts `runner_heartbeat_lost` +
`tool_call.crashed`. Any rename breaks QA — coordinate via
`SendMessage` before changing.

---

## Phased Implementation

Per stream. Tasks mapped to implementation-plan.md tracks A→H and
to the milestones M0→M6 in
[`../implementation-plan.md`](../implementation-plan.md). Tasks are
atomic — each is one PR-sized commit.

### Stream 1 — dev-1 (Backend Foundation)

#### Phase 1.A — Infra scaffold (Track A, milestone M0)

- [ ] **A1.** `pyproject.toml` (uv) with locked deps for the backend
      half: `fastapi`, `uvicorn[standard]`, `sqlalchemy>=2.0`,
      `asyncpg`, `alembic`, `aioboto3`, `pydantic-settings`, `httpx`,
      `pytest`, `pytest-asyncio`. Stream 2's deps come in via the
      bundled change-request (see Ownership table).
- [ ] **A2.** `src/settings.py` — single `Settings(BaseSettings)` class
      reading `DATABASE_URL`, `MINIO_*`, `ANTHROPIC_API_KEY`,
      `RUNNER_WARM_IDLE_S=60`, `RUNNER_HEARTBEAT_TIMEOUT_S=30`,
      `RUNNER_IMAGE_TAG`, `LLM_PROVIDER=anthropic`, `BACKEND_URL`,
      `MCP_URL`, `ARTIFACT_BUCKET`. Validation on boot.
- [ ] **A3.** `.env.example` — every var dev-1 owns + placeholders for
      dev-2 / dev-3 entries (filled by change-request).
- [ ] **A4.** `docker-compose.yml` — `postgres:16-alpine`, MinIO
      (`quay.io/minio/minio`), `mc-init` sidecar (`mc mb --ignore-existing`),
      `backend-migrate` one-shot, `backend` with `depends_on:
      service_completed_successfully`. Healthchecks per
      [`../research/04-persistence-storage.md`](../research/04-persistence-storage.md) §10.
- [ ] **A5.** `docker-compose.dev.yml` — bind-mount `src/` for hot reload;
      expose `5432`, `9000`, `9001`, `8000`.
- [ ] **A6.** `Dockerfile` — `python:3.12-slim`, uv install, `CMD
      ["opentelemetry-instrument", "uvicorn", "src.main:app", ...]`
      (the `opentelemetry-instrument` wrapping comes via dev-3
      change-request).
- [ ] **A7.** `src/main.py` skeleton — `FastAPI(lifespan=lifespan)`
      with empty lifespan and routers stubbed. Will be filled by
      dev-2 change-request later (runner registry, orphan sweep,
      warm-idle managers).
- [ ] **A8.** `src/api/health.py` — `/healthz` (200 always), `/readyz`
      (DB + S3 reachable).
- [ ] **A9.** **Exit criterion**: `docker compose up -d && curl
      localhost:8000/healthz` → 200.

#### Phase 1.B — Persistence layer (Track B, milestone M1)

- [ ] **B1.** `src/db/base.py` — `DeclarativeBase` + `MetaData` with
      naming convention from research/04 §3.3.
- [ ] **B2.** `src/db/engine.py` — `create_async_engine` +
      `async_sessionmaker(expire_on_commit=False)`.
- [ ] **B3.** `src/db/deps.py` — `get_session()` async generator.
- [ ] **B4.** `src/db/models.py` — ORM mappings for the 4 tables in
      investigation.md §4. Also: `HydrationPayload` + `McpStdioEndpoint`
      Pydantic models (Contract D) **so dev-2 can import the type
      definitions without pulling service code**.
- [ ] **B5.** Alembic init — `alembic.ini` + async `env.py`.
- [ ] **B6.** `migrations/versions/2026_05_14_0001_init.py` — autogen
      + manual review for `gen_random_uuid()` default, partial indexes
      `WHERE closed_at IS NULL` / `WHERE finalized_at IS NULL`, GIN
      `jsonb_path_ops`, named ENUM `message_role`.
- [ ] **B7.** `backend-migrate` compose service runs `alembic upgrade head`.
- [ ] **B8.** `src/repos/sessions.py` — `create`, `get`,
      `touch_updated_at`, `close`.
- [ ] **B9.** `src/repos/messages.py` — `append(role, content)`,
      `append_delta(session, message_id, delta)` doing the
      server-side `UPDATE messages SET content = content || $1` (the
      Contract A invariant), `finalize(message_id)`, `list_for_session(seq)`.
- [ ] **B10.** `src/repos/audit.py` — `append`,
      `last_state_snapshot(session_id)`, `list_for_session(after)`.
- [ ] **B11.** `src/repos/artifacts.py` — `register` (with `UNIQUE
      (session_id, object_key)` idempotent insert), `get`,
      `list_for_session`.
- [ ] **B12.** `src/storage/s3.py` — `aioboto3` lifespan-managed
      client on `app.state.s3`; `upload_bytes`, `presigned_get`.
- [ ] **B13.** Boot recovery — scan
      `messages WHERE finalized_at IS NULL` on startup, write
      `audit_log.event_type = 'stream_orphaned'` per orphaned row. (AC24.)
- [ ] **B14.** `tests/unit/test_repos.py` — basic CRUD on all 4 tables;
      `UNIQUE (session_id, object_key)` idempotency on artifacts.
- [ ] **B15.** **Exit criterion**: `alembic upgrade head` succeeds
      against the compose Postgres; unit tests green.

#### Phase 1.C — HTTP surface, dev-1 half (Track C — sessions/internal/webhooks/artifacts/stop, milestone M2)

- [ ] **C1-1.** `src/api/sessions.py` — `POST /v1/sessions`,
      `GET /v1/sessions/{id}`, `GET …/messages`,
      `POST …/messages` (writes user msg + commits + returns
      `{run_id, stream_url}`), `POST …/close`. (AC1, AC2, AC3.)
- [ ] **C1-2.** `src/api/internal.py` — `POST /internal/sessions/{id}/events`
      (chunked JSONL ingress; reads `request.stream()` line by line,
      pushes each frame into the `ExecutionRegistry` via `event_bus.publish`).
      `GET /internal/sessions/{id}/inbox` (long-poll for outbound user
      messages; 25 s budget). Localhost-only.
- [ ] **C1-3.** `src/hooks_audit/verify_hmac.py` — HMAC-SHA256 verify,
      60 s replay window.
- [ ] **C1-4.** `src/hooks_audit/policy.py` — PoC stub: `Policy.decide(event) -> {"decision": "allow"}`.
- [ ] **C1-5.** `src/api/webhooks.py` — `POST /v1/webhooks/runners/{rid}/events`:
      verify HMAC → write `audit_log` row → call `Policy.decide` →
      respond `202 {decision, reason?}`. Same DB transaction. (AC10, AC11.)
- [ ] **C1-6.** `src/api/artifacts.py` — `GET /v1/artifacts/{id}` →
      302 to presigned URL. (AC23.)
- [ ] **C1-7.** `src/api/stop.py` — `POST /v1/sessions/{id}/runs/{run_id}/cancel`
      → mark run cancelled, call `runner.cancel(handle)` if alive.
- [ ] **C1-8.** `tests/unit/test_hmac.py` — HMAC verify, expired ts, bad sig.
- [ ] **C1-9.** **Exit criterion**: every dev-1-owned route under `/v1/`
      and `/internal/` works against a fixture runner.

#### Phase 1.D — Apply dev-2's lifespan change-request (Track A, late M3)

- [ ] **C1-10.** Apply `src/main.py` change-request from dev-2:
      lifespan registers DB engine → S3 client → `RunnerRegistry()`
      (dev-2 stub on day 1, real later) → orphan-container sweep
      (dev-2) → eviction sweeper (dev-2). Order matters — see
      Boundary Ambiguity #10 below.

### Stream 2 — dev-2 (Runner + Skills + Streaming)

#### Phase 2.0 — Stub registry (CRITICAL, day 1, ahead of all else)

- [ ] **D0.** Ship `src/runner_mgmt/__init__.py` +
      `src/runner_mgmt/registry.py` with a no-op `RunnerRegistry` class
      exposing `__init__`, `get_or_spawn`, `release`, `shutdown_all`.
      This unblocks dev-1's `src/main.py` lifespan wiring.
      **Send the bundled deps change-request for `pyproject.toml`
      at the same time.**

#### Phase 2.A — Streaming layer (Track C half, milestone M2)

- [ ] **D-S1.** `src/streaming/event_bus.py` — in-proc pub/sub keyed by
      `session_id` (asyncio.Queue per subscriber, no external infra).
- [ ] **D-S2.** `src/streaming/execution_registry.py` — lift from Repo 2;
      buffers events keyed by `(session_id, run_id)`; cursor replay
      on resume; capped 10k events; 5-min eviction after `RUN_FINISHED`.
      (AC5, AC18.)
- [ ] **D-S3.** `src/streaming/agui_event_formatter.py` — lift from
      Repo 2; Strands → AG-UI mapping for any backend-side translation
      (most events come pre-formatted from runner via JSONL).
- [ ] **D-S4.** `src/streaming/sse.py` — `EventEncoder` wrapper +
      `StreamingResponse` helper; `data: ...\n\n` framing.
- [ ] **D-S5.** `src/streaming/debounce.py` — 256-char / 250-ms buffer
      per active assistant message; calls
      `MessageRepo.append_delta(session, message_id, delta_str)`
      (Contract A invariant). Flush on `TEXT_MESSAGE_END` →
      `MessageRepo.finalize(message_id)`. (AC4c.)
- [ ] **D-S6.** `src/api/stream.py` — `POST /v1/sessions/{id}/stream`
      (CopilotKit-compatible: accepts `RunAgentInput`) and
      `GET /v1/sessions/{id}/stream?run_id=...&last_event_id=...`
      (resume). The generator:
        1. Pulls last `UserMessage` out of `RunAgentInput`.
        2. `MessageRepo.append(user_msg)` + commit. (AC4a.)
        3. `RunnerRegistry.get_or_spawn(session_id, hydration_payload)`.
        4. `ExecutionRegistry.start_run(session_id, run_id)` if cursor==null;
           else `ExecutionRegistry.replay_from(cursor)`.
        5. `async for ev in registry.subscribe(...)`: persist via
           `Debouncer` for text deltas (AC4c) and via `MessageRepo` +
           `AuditRepo` for tool calls + state. Check
           `request.is_disconnected()` between yields. (AC5.)
- [ ] **D-S7.** `tests/unit/test_debounce.py` — 256-char threshold,
      250-ms threshold, flush on finalize.
- [ ] **D-S8.** `tests/unit/test_agui_formatter.py` — Strands signal →
      AG-UI event shape.

#### Phase 2.D — Runner (Track D, milestones M3, M4)

- [ ] **D1.** `runner/Dockerfile` — `python:3.12-slim`, uv install,
      `VOLUME ["/skills"]`, `ENV KLOC_HYDRATION_PATH=/run/kloc/hydration.json`,
      `ENTRYPOINT ["python", "-m", "runner"]`. (Plus
      dev-3 change-request to wrap CMD with `opentelemetry-instrument`.)
- [ ] **D2.** Build `kloc-agent-runner:<sha>` via `Makefile`
      `build-runner` target (owned by dev-1; dev-2 contributes the
      target's body via change-request).
- [ ] **D3.** `src/runner_mgmt/protocol.py` — `Runner` Protocol
      (5 methods) + `HydrationPayload` Pydantic re-export from
      `src/db/models.py`.
- [ ] **D4.** `src/runner_mgmt/hydrate.py` — write `HydrationPayload`
      JSON to `/tmp/hydration-<rid>.json`; build aiodocker
      `HostConfig.Mounts` entry (Bind, ReadOnly=True); clean tempfile
      in `terminate()`. (AC16.)
- [ ] **D5.** `src/runner_mgmt/docker_runner.py` — `DockerRunner`:
      `pull` → `create` (with `HostConfig` Mem 1 GiB, NanoCpus 2e9,
      PidsLimit 256, `RestartPolicy: no`, `kloc.role=runner` label,
      `<project>_default` network) → `start` → `attach` stream →
      `stop(t=5)` → `wait` → `delete(force=True)`. Implements
      `Runner` Protocol.
- [ ] **D6.** `src/runner_mgmt/registry.py` — replace stub with real
      `dict[session_id, RunnerHandle]`; `get_or_spawn(session_id,
      hydration_payload)` checks for live container; otherwise
      `DockerRunner.spawn(payload)`. Holds per-session
      `WarmIdleManager` + heartbeat watcher tasks.
- [ ] **D7.** `src/runner_mgmt/warm_idle.py` — `WarmIdleManager` per
      session; `on_run_finished()` schedules a `RUNNER_WARM_IDLE_S=60`
      kill task; `on_user_message()` cancels it; **`await self._task`
      before reuse decision to handle R6b race**. (AC13, AC14, AC15.)
- [ ] **D8.** `src/runner_mgmt/heartbeat.py` — per-session task that
      times out at `RUNNER_HEARTBEAT_TIMEOUT_S=30` → terminate +
      mark `sessions.runner_state='crashed'` + write
      `audit_log.runner_heartbeat_lost`. (AC20, AC21.)
- [ ] **D9.** `src/runner_mgmt/sweeper.py` — boot-time orphan sweep:
      `docker ps --filter label=kloc.role=runner` → kill+delete each.
      Registered in lifespan via dev-1's change-request from D0.
      (AC25.)
- [ ] **D10.** `runner/__main__.py` — entry: read hydration file → build
      agent via `agent_factory` → open `MCPClient` in a `with` block
      (lifecycle-scoped to the whole run) → enter `iter_inbound()`
      long-poll loop → on each user message, call
      `ag_adapter.run(RunAgentInput)` and `await emit(event)` per yield.
- [ ] **D11.** `runner/model_factory.py` — `LLM_PROVIDER` switch
      (`anthropic` default → `AnthropicModel(model_id=…)` —
      **explicit**; constraint 4). `openrouter` + `bedrock` stubbed.
- [ ] **D12.** `runner/mcp_clients.py` — `MCPClient(lambda:
      stdio_client(StdioServerParameters(command="uv",
      args=["run","kloc-intelligence","mcp-server","--database","demo"])))`.
      The `with` scope covers the whole session lifetime (R11).
- [ ] **D13.** `runner/agent_factory.py` — `create_agent(payload)`:
      build `Agent` with `model` from `model_factory`, `tools=[*mcp_tools,
      summarizer_agent]`, `system_prompt=base+skills_prompt`. **No
      `session_manager` passed**. `agent.hooks.add_callback(BeforeToolCallEvent,
      audit_callback)`. (AC6, AC7.)
- [ ] **D14.** `runner/hooks/audit.py` — Contract C sender. Async
      `audit_callback(event)`: build payload, sign HMAC, `httpx.AsyncClient.post(timeout=2.0)`,
      on `{decision:"deny"}` set `event.cancel_tool = reason`; on
      timeout set `event.cancel_tool = "policy_deadline_exceeded"` +
      emit `CustomEvent(name="HookBackpressure", ...)`. (AC10, AC12, AC19.)
- [ ] **D15.** `runner/hooks/utils.py` — `resolve_tool_call(event)`
      lift from Repo 2.
- [ ] **D16.** `runner/channel.py` — `emit(event)`: append to
      outbound buffer streamed via chunked `httpx.AsyncClient.post`
      (Transfer-Encoding: chunked). `iter_inbound()`: long-poll
      `GET /internal/sessions/{id}/inbox` (25 s budget). `heartbeat()`
      task: emit `{"type":"heartbeat","busy":bool,"ts":...}` every 15 s.
- [ ] **D17.** Wire `ag_ui_strands.StrandsAgent(strands_agent,
      StrandsAgentConfig(...))` in `agent_factory.py`;
      `ag_adapter.run(RunAgentInput)` is the async generator.
- [ ] **D18.** Crash policy — if runner dies mid-tool-call, emit
      `RUN_ERROR(code="STRANDS_ERROR", cause="runner_crashed")`;
      backend writes `tool_call.crashed` audit row. (AC20.)
- [ ] **D19.** `tests/unit/test_warm_idle.py` — timer expiry,
      cancellation, R6b race (kill mid-flight + new message).

#### Phase 2.E — Skills (Track E, milestone M5)

- [ ] **E1.** `skills/summarize-callgraph/SKILL.md` — frontmatter
      `name`, `description` (concise so the LLM hits it for matching
      questions). (AC8.)
- [ ] **E2.** `runner/agent_factory.py`: call `discover_skills(Path("/skills"))`
      → `generate_skills_prompt(skills)` → inject into `Agent.system_prompt`.
- [ ] **E3.** Register `file_read` from `strands_tools` in `Agent.tools`
      so the LLM can progressively load SKILL.md body. (AC9.)
- [ ] **E4.** Verify empirical progressive disclosure via OTel
      console output during dev (open question OQ in
      [`../investigation.md`](../investigation.md) §8).
- [ ] **E5.** **(C4 from QA — two-pronged assertion for AC9)**
      Verify on first integration test run whether
      `BeforeToolCallEvent` fires for `strands_tools.file_read` —
      this is the *primary* assertion target for QA scenario 5
      (skill load): audit row with `event_type=tool_call.started`,
      `payload.tool_name=file_read`. If empirically the hook does
      NOT fire on `file_read` (Strands SDK open question per inv §8),
      the **fallback** is dev-3's Track H — OTel span attribute
      `tool.name=file_read` must be visible. dev-2 reports
      empirical result to dev-3 + QA via `SendMessage`. The plan
      does not gate on the answer; QA's test (G13 / e2e vertical
      slice) checks **either** signal is present.

### Stream 3 — dev-3 (Frontend + Observability)

#### Phase 3.A — Frontend scaffold (Track F, milestone M2 — can start in parallel against a fake backend)

- [ ] **F1.** Scaffold from
      `CopilotKit/CopilotKit @ examples/integrations/strands-python`
      or `npx copilotkit create -f aws-strands-py`. Result lives at
      `frontend/`.
- [ ] **F2.** Pin frontend deps: `@copilotkit/runtime 1.52.1`,
      `@copilotkit/react-core 1.52.1`, `@copilotkit/react-ui 1.52.1`,
      `@ag-ui/client 0.0.42` (**exact** — pair-pinned with
      `ag-ui-protocol==0.1.18` in pyproject.toml; AC26),
      `next 16.0.8`, `react ^19.2.1`, `zod`.
- [ ] **F3.** `frontend/src/app/layout.tsx` — `<CopilotKit
      runtimeUrl="/api/copilotkit" agent="kloc_agent">`.
- [ ] **F4.** `frontend/src/app/page.tsx` — `<CopilotSidebar>` +
      `useCoAgent<KlocAgentState>` + `useRenderToolCall` (for `kloc_*`
      MCP tool cards via `ToolCallCard.tsx`).
- [ ] **F5.** `frontend/src/app/api/copilotkit/route.ts` —
      `CopilotRuntime + HttpAgent({ url: AGENT_PROXY_URL })` +
      `copilotRuntimeNextJSAppRouterEndpoint`.
- [ ] **F6.** `frontend/src/app/api/agent-proxy/route.ts` — builds
      `RunAgentInput` envelope (`thread_id`, `run_id`, `messages` with
      UUIDs, `tools`, `context`, `state`, `forwarded_props`); fetches
      `BACKEND_URL/v1/sessions/{id}/stream`; proxies SSE. Lift from
      Repo 4. (Critical glue — without this CopilotKit's `HttpAgent`
      doesn't speak AG-UI.)
- [ ] **F7.** `frontend/src/lib/api.ts` — `createSession`,
      `listMessages`, `closeSession` REST helpers.
- [ ] **F8.** `frontend/src/utils/sseParser.ts` — lift from Repo 2;
      not on the critical path (CopilotKit's runtime parses for us)
      but kept for fallback / future non-Copilot pages.
- [ ] **F9.** `frontend/Dockerfile` — Next.js prod build. Send
      change-request to dev-1 for `docker-compose.yml` frontend service.
- [ ] **F10.** Manual smoke: open `localhost:3000`, send message,
      observe text stream + tool-call card render. (Match the vertical
      slice in spec § Scope.)

#### Phase 3.H — Observability (Track H, milestone M6)

- [ ] **H1.** Send change-request to dev-1 for `Dockerfile`: wrap
      backend CMD with `opentelemetry-instrument` (already in A6
      but verify).
- [ ] **H2.** Send change-request to dev-2 for `runner/Dockerfile`:
      same `opentelemetry-instrument` wrap on the ENTRYPOINT.
- [ ] **H3.** Send change-request to dev-1 for `.env.example`: add
      `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME=kloc-agent`,
      `OTEL_TRACES_EXPORTER=console` (dev default).
- [ ] **H4.** Send change-request to dev-2 for
      `runner/__main__.py`: call `StrandsTelemetry().setup_console_exporter()`
      on boot for dev parity (per `Skill(strands-core-concepts)` →
      observability resource).
- [ ] **H5.** Verify hook callbacks correlate with OTel tool-call
      spans (investigation.md §7 R13 / §8 open question).

### QA — qa (Track G, ongoing alongside M0-M6)

- [ ] **G1.** `tests/pytest.ini` — `asyncio_mode=auto`, markers
      `unit, integration, e2e`, default deselect `e2e`.
- [ ] **G2.** `tests/conftest.py` — fixtures: `db` (rollback session),
      `s3` (MinIO client against compose), `client` (TestClient with
      lifespan), `runner_mock` (in-proc fake `Runner` impl).
- [ ] **G3.** `tests/fixtures/` — `mock_model_provider.py`,
      `mock_session_manager.py`, `mock_tools.py`, `mock_runner.py`.
      Lift shapes from Repo 2.
- [ ] **G4.** `tests/integration/test_docker_runner.py` — real spawn
      + JSONL round-trip + warm-idle eviction + heartbeat-timeout
      terminate. (QA7, QA11.)
- [ ] **G5.** `tests/integration/test_sse_encoder.py` — verify wire
      format matches AG-UI 0.1.18 (`data: {...}\n\n`). (QA2.)
- [ ] **G6.** `tests/integration/test_resume_after_disconnect.py` —
      disconnect mid-stream, reconnect with `last_event_id`, assert
      replay then live. (QA9 / AC5+AC18.)
- [ ] **G7.** `tests/integration/test_rehydrate.py` — evict runner,
      send new message, assert `HydrationPayload` written, container
      respawned, `RunAgentInput.messages` rebuilds prior history,
      model references prior turn. Cold-start budget ≤ 2 s. (QA8 /
      AC16+AC17.)
- [ ] **G8.** `tests/integration/test_hook_deny.py` — swap
      `Policy.decide` to deny one tool; assert
      `event.cancel_tool = reason` is honored and
      `TOOL_CALL_RESULT` carries denial. (QA10 / AC19.)
- [ ] **G9.** `tests/integration/test_concurrent_sessions.py` — two
      sessions, distinct containers, no cross-talk in audit /
      messages / `ExecutionRegistry`. (QA12 / AC22.)
- [ ] **G10.** `tests/integration/test_orphan_sweep.py` — fixture
      pre-creates orphan container labelled `kloc.role=runner`;
      assert boot-time sweeper kills + removes it. (QA11 / AC25.)
- [ ] **G11.** `tests/integration/test_dependency_pinning.py` — parse
      `pyproject.toml` + `frontend/package.json`; assert exact
      versions for `ag-ui-protocol == 0.1.18` and
      `@ag-ui/client == 0.0.42`. (QA14 / AC26.)
- [ ] **G12.** `tests/e2e/sse_client.py` — lift from Repo 2.
- [ ] **G13.** `tests/e2e/test_vertical_slice.py` — full PoC: send
      "Find handlers of OrderPlaced and summarise them" → assert MCP
      call fires (AC6), sub-agent invoked (AC7), skill loaded (AC8/9),
      audit rows present (AC10), message persisted (AC4c). (QA1-QA8.)
- [ ] **G14.** **Exit criterion**: `pytest -m "not e2e"` green in CI;
      `pytest -m e2e` green locally against compose stack.

---

## Dependency Graph

```
                                  ┌── dev-1 / Phase 1.A (Track A) ── M0
                                  │     │
                                  │     ▼
                                  │   dev-1 / Phase 1.B (Track B) ── M1
                                  │     │
   dev-2 / Phase 2.0 (stub) ──────┤     │
                                  │     ▼
                                  │   dev-1 / Phase 1.C dev-1-half ── M2 (server up, fake runner)
                                  │     │
   dev-2 / Phase 2.A (Track C-half + streaming) ─┴──┐
                                                    │
                                                    ▼
                                  dev-2 / Phase 2.D (Track D) ── M3 (real runner end-to-end)
                                                    │
                                                    ├─► dev-2 / Phase 2.E (Track E) ── M5
                                                    │
                                                    ▼
   QA / Phase 3.G  (G4..G11, especially G7) ── M4 (warm-idle + rehydrate proven)

   dev-3 / Phase 3.A (Track F)  parallel from M0,
       converges at M6 when end-to-end runs in a browser.

   dev-3 / Phase 3.H (Track H)  ──────────────► M6 (PoC complete)
```

Stream dependencies:

- **Stream 1 (dev-1)** is the longest critical path through M0 → M1 → M2.
- **Stream 2 (dev-2)** Phase 2.0 stub ships day 1 → unblocks dev-1's
  lifespan wiring (Phase 1.A7 / 1.C-10). Phase 2.A streaming/ can
  proceed once dev-1 ships `repos/messages.py:append_delta` (Phase 1.B9).
  Phase 2.D blocks on dev-1's `src/api/internal.py` (Phase 1.C-1.2)
  and on `kloc-agent-runner` image build.
- **Stream 3 (dev-3)** Phase 3.A can scaffold against a fake backend
  immediately (start at M0, converge at M6). Phase 3.H is end-of-line.
- **QA** runs across all phases; integration tests in Phase 3.G come
  online progressively as features merge.

---

## Test Cases

Descriptive, not code. Every AC maps to at least one test case in
`tests/`. Each test case is one of {unit, integration, e2e}.

| AC | Scenario | Test type | File |
|---|---|---|---|
| AC1 | POST /v1/sessions creates row + audit | unit | `tests/unit/test_repos.py` + `tests/integration/test_sessions_api.py` (G14 parent) |
| AC2 | GET /v1/sessions/{id}/messages paginates by seq | unit + integration | as above |
| AC3 | POST /…/close blocks subsequent streams | integration | as above |
| AC4a | User msg persisted before forward to runner | integration | `tests/integration/test_sse_encoder.py` (G5) |
| AC4b | SSE frames are valid AG-UI 0.1.18 (`data: {...}\n\n`) | integration | G5 |
| AC4c | Text deltas persisted via 256-char/250-ms debounce | unit + integration | `tests/unit/test_debounce.py` (D-S7) + G5 |
| AC5 | Browser disconnect breaks SSE; runner continues; events buffered | integration | G6 |
| AC6 | LLM-emitted MCP tool call round-trips via stdio JSON-RPC | e2e | G13 |
| AC7 | Sub-agent delegation routes through `BeforeToolCallEvent` hook | e2e | G13 |
| AC8 | `discover_skills(/skills)` enumerates SKILL.md + prompt injected | unit | `tests/unit/test_skills_load.py` (under dev-2) |
| AC9 | LLM `file_read`s SKILL.md body during run — two-pronged: primary `BeforeToolCallEvent` → audit row `tool_call.started` w/ `tool_name=file_read`; **OR** fallback OTel span `tool.name=file_read` (C4) | e2e | G13 |
| AC10 | BeforeToolCall posts HMAC webhook; audit row persisted before respond | integration | `tests/integration/test_hook_audit.py` (G14 parent) |
| AC11 | Webhook rejects bad HMAC / stale timestamp | unit | `tests/unit/test_hmac.py` (C1-8) |
| AC12 | 2s deadline → deny + HookBackpressure CustomEvent | integration | `tests/integration/test_hook_backpressure.py` (G14 parent) |
| AC13 | 60 s of silence → DockerRunner.terminate + audit row | integration | G4 (warm-idle eviction) |
| AC14 | New user message inside window cancels timer + reuses container | integration | G4 |
| AC15 | R6b race: kill mid-flight + new message → await kill task, fall through to cold-spawn | unit | `tests/unit/test_warm_idle.py` (D19) |
| AC16 | Rehydrate writes HydrationPayload + bind-mounts at /run/kloc/hydration.json | integration | G7 |
| AC17 | Cold-start ≤ 2s before first token; model references prior turn | integration | G7 |
| AC18 | last_event_id replay catches up then continues live | integration | G6 |
| AC19 | Policy "deny" → event.cancel_tool set → TOOL_CALL_RESULT carries denial | integration | G8 |
| AC20 | Heartbeat-dead at 30s → terminate + runner_state=crashed + audit | integration | G4 (heartbeat half) |
| AC21 | Crashed runner + new message → rehydrate (AC16 path) + "interrupted" surface | integration | combined G4 + G7 |
| AC22 | Two concurrent sessions → two distinct containers, no cross-talk | integration | G9 |
| AC23 | Artifact upload + presigned GET round-trips | integration | `tests/integration/test_artifacts.py` (G14 parent) |
| AC24 | Boot scan of `finalized_at IS NULL` writes `stream_orphaned` audit | unit | `tests/unit/test_boot_recovery.py` (B13/B14 parent) |
| AC25 | Boot orphan-container sweep kills + removes labelled containers | integration | G10 |
| AC26 | ag-ui-protocol 0.1.18 ↔ @ag-ui/client 0.0.42 pin enforced | unit | G11 |

QA scenarios (QA1-QA14) are the descriptive groupings in
[`kloc-agent-poc.md`](kloc-agent-poc.md) § QA scenario map. This
plan does not re-author them.

---

## Risks & Mitigations

Refers to [`../investigation.md`](../investigation.md) §7 R1–R13;
non-duplicating, only annotations that affect the plan.

| Risk | Source | Plan-level mitigation |
|---|---|---|
| **R1** — `strands_agentskills` not on PyPI | inv §7 R1 | dev-2's bundled deps change-request (Phase 2.0) pins to a specific commit SHA. Vendor in `src/vendor/` if upstream API churns mid-PoC. |
| **R2** — Strands silently defaults to Bedrock | inv §7 R2 / constraint 4 | `runner/model_factory.py` (D11) constructs `AnthropicModel(...)` explicitly when `LLM_PROVIDER=anthropic`. CI test asserts the agent is constructed with an explicit model. |
| **R3** — `MESSAGES_SNAPSHOT` O(N²) bandwidth | inv §7 R3 | Out of scope for PoC implementation; **measurement target locked (C5 from QA): if total `MESSAGES_SNAPSHOT` bytes ≥ 1 MB at turn 10, v2 MUST adopt page-by-page snapshots**. QA scenario 9 records cumulative snapshot bytes per turn during integration runs as a metric (no test gate). Backlog: `StrandsAgentConfig.emit_messages_snapshot=False` + client-side reconstruction. |
| **R4** — Mid-flight tool call on crash | inv §7 R4 | `runner/hooks/audit.py` (D14) and `src/runner_mgmt/heartbeat.py` (D8) mark `tool_call.crashed`; UI surfaces "interrupted, retry?". No auto-replay. |
| **R5** — AG-UI no semver | inv §7 R5 | **AC26** pair-pin enforced in CI (test G11). |
| **R6b** — Warm-idle race | inv §7 R6b | `WarmIdleManager.on_user_message()` (D7) awaits in-flight kill task before reuse decision. Unit test D19. |
| **R6c** — Cold-start 1-2 s | inv §7 R6c | Acceptable per spec AC17 (≤ 2 s). Pre-warm explicitly out of scope (spec § Out of Scope). |
| **R6d** — Skills mount drift | inv §7 R6d | `./skills/` is a read-only mount; mutations across spawns require dropping in-flight sessions (documented in `README.md`). |
| **R8** — Hook webhook deadline + backend slowness | inv §7 R8 | **Contract C** specifies 2 s deadline + `HookBackpressure` CustomEvent on timeout. (AC12.) `AfterToolCall` fire-and-forget with bounded queue (256), drop heartbeats first. |
| **R9** — Concurrent backend replicas race migrations | inv §7 R9 | PoC is single-replica + `backend-migrate` is a one-shot `service_completed_successfully` dependency. (Phase 1.A4.) |
| **R10** — Orphan MinIO uploads | inv §7 R10 | Documented as future nightly sweep. PoC: rely on `UNIQUE(session_id, object_key)` for idempotency. |
| **R11** — `MCPClient` context exit kills subprocess | inv §7 R11 | `runner/__main__.py` (D10) wraps the *entire session lifetime* in the `with` block. Documented inline. |
| **R12** — Multi-replica + in-proc registry | inv §7 R12 | Single-replica per spec § Out of Scope. Future: Postgres or Redis registry behind same Protocol. |
| **R13** — Hook ↔ OTel span correlation unverified | inv §7 R13 / inv §8 | Phase 3.H5 verifies empirically via console exporter. |
| **NEW — §6 stale wording** | architect note A | Investigation.md §6 says vertical PoC "does not include Docker runner (subprocess only)" — stale from a pre-Docker-only draft. **§2.1 is canonical**: Docker is the only mode. Spec and this plan both follow §2.1. Flagged here so PM/QA don't get confused. |

---

## Operational notes for developers

- **Phase 2.0 is non-negotiable**: dev-2 ships the stub `RunnerRegistry`
  and the bundled `pyproject.toml` deps change-request **before**
  starting Track D. This unblocks dev-1 from waiting on the lifespan
  contract.
- **Change-request convention** (`SendMessage` to file owner):
  - Subject: `change-request: <file path>`
  - Body: exact lines to add/remove (use unified-diff style).
  - Owner applies in one commit; pings requester on completion.
- **Stand-down for architect**: after this plan v1 is accepted by
  team-lead, no v2 polishing. Plan amendments only on real
  developer-flagged ambiguities.

---

## Cross-references

- Spec: [`kloc-agent-poc.md`](kloc-agent-poc.md) (26 ACs + 14 QA scenarios).
- Investigation: [`../investigation.md`](../investigation.md) (§2.1 locked decisions, §3 module layout, §4 schema, §5 REST, §7 risks).
- Architecture: [`../architecture.md`](../architecture.md) (§3.3 warm-idle state machine, §3.4 rehydrate flow + cold-start budget).
- Implementation plan: [`../implementation-plan.md`](../implementation-plan.md) (Tracks A→H, M0→M6 milestones, dep graph).
- Research briefs: `../research/01-strands-minimal.md` (Strands minimal API), `../research/02-backend-agui.md` (AG-UI critical path), `../research/03-runner-mgmt.md` (Docker runner internals), `../research/04-persistence-storage.md` (Postgres + MinIO), `../research/05-reference-projects.md` (Repo 1-4 lift catalogue).
