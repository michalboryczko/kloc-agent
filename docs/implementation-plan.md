# kloc-agent — Implementation Plan

> Checkbox plan. No code — each item is one line + a reference. The
> references point at the doc that explains **how** to do it. When in doubt,
> read the referenced section first; ask a question if it's still unclear.
>
> **Acceptance for the PoC**: the vertical slice in
> [`investigation.md`](investigation.md) §6 runs end-to-end.

---

## Conventions for this plan

- `[ ]` = todo, `[x]` = done, `[~]` = in progress, `[!]` = blocked
- **Ref** column points at the *primary* doc + section. When an item spans
  two docs, both are listed.
- Each track has an **Exit criterion** — a single, observable thing that
  proves the track is done.
- Tracks A→H are designed to be doable in order. Where work can be done in
  parallel, the dependency graph (§ Bottom) calls it out.

---

## Track A — Infra scaffold

**Goal**: empty repo → `docker compose up` brings up postgres + minio + a
healthcheck-only FastAPI on `:8000`.

| # | Item | Ref |
|---|------|-----|
| A1 | Init `pyproject.toml` with uv, pin all the locked package versions | [`README.md`](README.md) quick-reference |
| A2 | Pin `strands-agents 1.39.0`, `ag-ui-protocol 0.1.18`, `ag_ui_strands 0.1.8`, `strands_agentskills @ git+…@<sha>` (vendored or pinned) | [`investigation.md`](investigation.md) §2.1, §7 R1 |
| A3 | `src/settings.py` — single `Settings` class via `pydantic-settings`; load from env | [`investigation.md`](investigation.md) §2.1, [`research/05-reference-projects.md`](research/05-reference-projects.md) cross-repo synthesis |
| A4 | `.env.example` with all required vars (DB, MinIO, Anthropic key, `RUNNER_WARM_IDLE_S`, `RUNNER_HEARTBEAT_TIMEOUT_S`, `LLM_PROVIDER`, `BACKEND_URL`, `MCP_URL`) | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §10 |
| A5 | `docker-compose.yml` — postgres + minio + `mc-init` sidecar + `backend-migrate` one-shot + backend, all with healthcheck-driven `depends_on` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §10 |
| A6 | `docker-compose.dev.yml` overrides — volume bind-mount `src/` for hot reload, expose ports | [`research/05-reference-projects.md`](research/05-reference-projects.md) cross-repo synthesis |
| A7 | Backend `Dockerfile` — `python:3.12-slim`, uv-install, `CMD ["opentelemetry-instrument","uvicorn","src.main:app",…]` | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.7 |
| A8 | `src/main.py` skeleton — FastAPI app + `lifespan` registering DB engine, S3 client (`app.state.s3`), runner registry, boot-time orphan-container sweeper | [`investigation.md`](investigation.md) §3; [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §2.6 |
| A9 | `src/api/health.py` — `/healthz` (liveness), `/readyz` (DB + S3 reachable) | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.10 |
| A10 | Boot the stack end-to-end; `curl /healthz` → 200; MinIO console reachable on `:9001` | — |

**Exit criterion**: `docker compose up -d` + `curl localhost:8000/healthz` → 200.

---

## Track B — Persistence layer

**Goal**: schema migrated, repos available, batched-write debounce ready.

| # | Item | Ref |
|---|------|-----|
| B1 | `src/db/base.py` — `DeclarativeBase` + `MetaData(naming_convention=…)` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §3.3 |
| B2 | `src/db/engine.py` — `create_async_engine` + `async_sessionmaker(expire_on_commit=False)` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §3.3 |
| B3 | `src/db/deps.py` — `get_session()` async generator for `Depends(...)` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §3.3 |
| B4 | `src/db/models.py` — `Session`, `Message`, `AuditLog`, `ArtifactMetadata` ORM mappings; pgcrypto + named enum `message_role` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §1, [`investigation.md`](investigation.md) §4 |
| B5 | Alembic init — `alembic.ini` + async `env.py` reading `Settings.database_url` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §4.2 |
| B6 | First migration `2026_XX_XX_0001_init.py` — autogen, then **read it** before applying | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §4.3, §4.5 |
| B7 | `backend-migrate` compose service runs `alembic upgrade head`; backend `depends_on` gates on it | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §4.4 |
| B8 | `src/repos/sessions.py` — `create`, `get`, `list_open`, `close`, `touch_updated_at` | [`investigation.md`](investigation.md) §3 |
| B9 | `src/repos/messages.py` — `append`, `list_for_session(seq)`, `list_streaming` (the `finalized_at IS NULL` set) | [`investigation.md`](investigation.md) §4 invariants |
| B10 | `src/repos/audit.py` — `append`, `last_state_snapshot(session_id)`, `list_for_session(after)` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §1.4 |
| B11 | `src/repos/artifacts.py` — `register`, `get`, `list_for_session` with `UNIQUE(session_id, object_key)` idempotency | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §1.5 |
| B12 | `src/storage/s3.py` — `aioboto3` upload + `generate_presigned_url`, using lifespan-managed `app.state.s3` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §6.3, §6.4 |
| B13 | Streaming-write helper — buffered append with **256-char / 250-ms** debounce; flush on `TEXT_MESSAGE_END` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §2.2, [`investigation.md`](investigation.md) §5 |
| B14 | Backend boot recovery — scan `messages WHERE finalized_at IS NULL`, mark orphaned (audit row `stream_orphaned`) | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §2.3 |

**Exit criterion**: `alembic upgrade head` succeeds against the compose
Postgres; smoke test inserts + reads a Session + Message via the repos.

---

## Track C — Backend HTTP surface

**Goal**: routes accept the analyst, persist user messages, route to / from
runners, stream SSE back.

| # | Item | Ref |
|---|------|-----|
| C1 | `src/api/sessions.py` — REST CRUD per the locked shape (`POST /v1/sessions`, `GET …`, `GET …/messages`, `POST …/messages`, `POST …/close`) | [`investigation.md`](investigation.md) §5; [`research/02-backend-agui.md`](research/02-backend-agui.md) §6 |
| C2 | `src/streaming/sse.py` — `EventEncoder` wrapper, `StreamingResponse` helper that yields raw SSE bytes | [`research/02-backend-agui.md`](research/02-backend-agui.md) §1 |
| C3 | `src/streaming/execution_registry.py` — buffer events keyed by `(session_id, run_id)`, cursor replay on resume; capped at 10k events, 5-min eviction after RUN_FINISHED (lift from repo 2) | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.10 |
| C4 | `src/streaming/agui_event_formatter.py` — Strands signal → AG-UI event mapping (lift from repo 2; not strictly needed if `ag_ui_strands` does it for us — decide on Track D) | [`research/02-backend-agui.md`](research/02-backend-agui.md) §3 |
| C5 | `src/api/stream.py` — `POST /v1/sessions/{id}/stream` (CopilotKit-compatible) + `GET /v1/sessions/{id}/stream?last_event_id=…` (resume); **persist-then-yield** ordering; check `request.is_disconnected()` inside the generator | [`research/02-backend-agui.md`](research/02-backend-agui.md) §1, §7 |
| C6 | `src/api/internal.py` — `POST /internal/sessions/{id}/events` (chunked JSONL ingress from runner) + `GET /internal/sessions/{id}/inbox` (long-poll outbound user messages); localhost-bound, no public auth | [`investigation.md`](investigation.md) §5; [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §3.2 |
| C7 | `src/api/webhooks.py` — `POST /v1/webhooks/runners/{rid}/events` with **HMAC-SHA256** over `timestamp.body`, ≤ 60 s replay window; sync `Before*` decisions, fire-and-forget `After*` | [`research/02-backend-agui.md`](research/02-backend-agui.md) §8 |
| C8 | `src/api/stop.py` — `POST /v1/sessions/{id}/runs/{run_id}/cancel` → marks the run cancelled, calls `runner.cancel()` if alive | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.10 |
| C9 | `src/api/artifacts.py` — `GET /v1/artifacts/{id}` → presigned URL via `app.state.s3` | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §6.3 |
| C10 | Resume semantics: re-emit `MESSAGES_SNAPSHOT` (built from DB) on reconnect; cursor via `Last-Event-ID` or `last_event_id` query param | [`research/02-backend-agui.md`](research/02-backend-agui.md) §7 |
| C11 | Persistence ordering invariants enforced in `stream.py`: user message persisted + committed *before* runner forward; assistant deltas flushed before yield; hook audit row written *before* responding to webhook | [`investigation.md`](investigation.md) §5 |

**Exit criterion**: `curl -X POST /v1/sessions` + `curl -N -X POST /v1/sessions/{id}/stream` with a fixture runner mock yields SSE frames; messages land in the DB.

---

## Track D — Runner (Docker-only) + warm-idle lifecycle

**Goal**: backend can spawn a container per session, send/receive messages,
warm-idle evict after 60 s, rehydrate cleanly on the next message.

| # | Item | Ref |
|---|------|-----|
| D1 | `runner/Dockerfile` — `python:3.12-slim`, uv install, `VOLUME ["/skills"]`, env `KLOC_HYDRATION_PATH=/run/kloc/hydration.json`, `ENTRYPOINT ["python","-m","runner"]` | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §2.7 |
| D2 | Build `kloc-agent-runner:<sha>` in compose (or in CI); backend pulls/sees it via local daemon | [`investigation.md`](investigation.md) §9 Track D |
| D3 | `src/runner_mgmt/protocol.py` — one-line `Runner` Protocol + `HydrationPayload` dataclass | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §4 |
| D4 | `src/runner_mgmt/hydrate.py` — write `HydrationPayload` to `/tmp/hydration-<rid>.json`; build aiodocker bind-mount config; clean tempfile on terminate | [`architecture.md`](architecture.md) §3.4 |
| D5 | `src/runner_mgmt/docker_runner.py` — full `DockerRunner` impl: pull, create with `HostConfig` (Mem 1 GiB, NanoCpus 2e9, PidsLimit 256, `RestartPolicy: no`, `kloc.role=runner` label, joins `<project>_default` network), start, attach event stream, terminate, delete | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §2.1–§2.6 |
| D6 | `src/runner_mgmt/registry.py` — `dict[session_id, RunnerHandle]`; `get_or_spawn(session_id)` checks for live + healthy container; otherwise spawns fresh with hydration | [`architecture.md`](architecture.md) §3.4 |
| D7 | `src/runner_mgmt/warm_idle.py` — `WarmIdleManager` per session: `on_run_finished()` schedules `RUNNER_WARM_IDLE_S=60` kill task; `on_user_message()` cancels it; handle the spawn-vs-reuse race (R6b) | [`architecture.md`](architecture.md) §3.3 |
| D8 | `src/runner_mgmt/heartbeat.py` — per-session watcher; `RUNNER_HEARTBEAT_TIMEOUT_S=30` → terminate + mark `crashed` | [`architecture.md`](architecture.md) §3.3 |
| D9 | Boot-time orphan sweep — `docker ps --filter label=kloc.role=runner`, kill+delete each (their sessions are stateless from our perspective) | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §2.6 |
| D10 | `runner/__main__.py` — read hydration file, build agent via `agent_factory`, open `MCPClient` in a `with` block, enter inbound long-poll loop | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §1 |
| D11 | `runner/model_factory.py` — `LLM_PROVIDER` env switch: `anthropic` (default) → `AnthropicModel`, `openrouter`, `bedrock` | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §9 gotcha; [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 3 §3.10 |
| D12 | `runner/mcp_clients.py` — `MCPClient(lambda: stdio_client(StdioServerParameters(command="uv", args=["run","kloc-intelligence","mcp-server","--database",…])))` | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §6 |
| D13 | `runner/agent_factory.py` — build `strands.Agent` with model, MCP tools, sub-agent (agents-as-tools), skills system prompt; **no `session_manager`** | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §1, §3, §7 |
| D14 | `runner/hooks/audit.py` — register `BeforeToolCallEvent` callback; on fire, `httpx.post(BACKEND_URL/v1/webhooks/runners/{rid}/events, hmac=…)`; honor `decision: deny` by setting `event.cancel_tool = "..."` | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §4; [`research/02-backend-agui.md`](research/02-backend-agui.md) §8 |
| D15 | `runner/hooks/utils.py` — `resolve_tool_call(event)` helper (lifted from repo 2) — unwraps `skill_executor` so the hook sees the underlying tool name | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.10 |
| D16 | `runner/channel.py` — outbound chunked POST to `BACKEND_URL/internal/sessions/{id}/events` (JSONL body), inbound long-poll `GET …/inbox`, heartbeat every 15 s | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §3.2; [`architecture.md`](architecture.md) §3 |
| D17 | Wire `ag_ui_strands.StrandsAgent(strands_agent, StrandsAgentConfig(...))` as the AG-UI adapter; `agent.run(RunAgentInput)` is the async generator of AG-UI events to emit | [`research/02-backend-agui.md`](research/02-backend-agui.md) §3 |
| D18 | Hook crash policy — if the runner dies mid-`TOOL_CALL_END → TOOL_CALL_RESULT`, surface `RUN_ERROR(code="STRANDS_ERROR", cause="runner_crashed")`; backend marks `tool_call.crashed` in audit | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §7 |

**Exit criterion**: `POST /v1/sessions/{id}/messages` triggers a real
container spawn, `agent.run()` emits AG-UI events back to the backend, and
the warm-idle timer fires + kills the container after 60 s of silence. A
follow-up message within 60 s reuses the same container; a follow-up after
60 s rehydrates cleanly with full history.

---

## Track E — Skills

**Goal**: at least one skill loadable via progressive disclosure.

| # | Item | Ref |
|---|------|-----|
| E1 | `./skills/` directory at repo root, bind-mounted into the runner at `/skills:ro` | [`investigation.md`](investigation.md) §3, [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §2.7 |
| E2 | One demo `SKILL.md` — e.g. `skills/summarize-callgraph/SKILL.md` with frontmatter `name`, `description`, optional `allowed-tools` | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §5 |
| E3 | `runner/agent_factory.py` calls `discover_skills(Path("/skills"))` → `generate_skills_prompt(skills)` → injects into `Agent.system_prompt` | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §1, §5 |
| E4 | Add `file_read` from `strands_tools` to `Agent.tools` so the LLM can progressively load the SKILL.md body | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §5 |
| E5 | Verify progressive disclosure empirically — LLM reads the body only when relevant | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §10 open question |
| E6 | (Optional, deferred) Adopt repo 2's `src/skill/{decorators,skill_registry,skill_tools}.py` for `@skill("name")` decorator + skill_executor pattern | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.10 |

**Exit criterion**: with the demo skill present, an analyst question that
matches its description triggers a `file_read` of the SKILL.md body during
the run.

---

## Track F — Frontend

**Goal**: a browsable chat that round-trips one message through the system.

| # | Item | Ref |
|---|------|-----|
| F1 | Scaffold via `npx copilotkit create -f aws-strands-py` OR copy from `CopilotKit/CopilotKit @ examples/integrations/strands-python` | [`research/02-backend-agui.md`](research/02-backend-agui.md) §5; [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 4 §4.1 |
| F2 | Pin frontend deps: `@copilotkit/runtime 1.52.1`, `@copilotkit/react-{core,ui} 1.52.1`, `@ag-ui/client ^0.0.42`, `next 16.0.8`, `react ^19.2.1` | [`research/02-backend-agui.md`](research/02-backend-agui.md) §5 |
| F3 | `src/app/layout.tsx` — `<CopilotKit runtimeUrl="/api/copilotkit" agent="kloc_agent">` | [`research/02-backend-agui.md`](research/02-backend-agui.md) §4 |
| F4 | `src/app/page.tsx` — `<CopilotSidebar>` + `useCoAgent<KlocAgentState>` for shared state + `useRenderToolCall` for MCP tool cards | [`research/02-backend-agui.md`](research/02-backend-agui.md) §4 |
| F5 | `src/app/api/copilotkit/route.ts` — `CopilotRuntime` + `HttpAgent({ url: AGENT_PROXY_URL })` + `copilotRuntimeNextJSAppRouterEndpoint` | [`research/02-backend-agui.md`](research/02-backend-agui.md) §4; [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 4 §4.1 |
| F6 | `src/app/api/agent-proxy/route.ts` — receives CopilotKit calls, **builds the `RunAgentInput` envelope** (`thread_id`, `run_id`, `messages` with UUIDs, `tools`, `context`, `state`, `forwarded_props`), fetches `BACKEND_URL/v1/sessions/{id}/stream`, proxies SSE | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 4 §4.1 |
| F7 | `src/lib/api.ts` — session lifecycle REST helpers (`createSession`, `listMessages`, `closeSession`) | [`investigation.md`](investigation.md) §5 |
| F8 | `src/utils/sseParser.ts` — lift from repo 2; only needed if any non-CopilotKit route consumes AG-UI SSE directly | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.4 |
| F9 | Frontend `Dockerfile` — Next.js prod build; add to `docker-compose.yml` | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.7 |
| F10 | Manual smoke: open `localhost:3000`, type a message, watch text stream + tool-call card render | — |

**Exit criterion**: an analyst can complete the §6 vertical slice in a real browser.

---

## Track G — Tests

**Goal**: enough coverage to catch regressions on the vertical slice.

| # | Item | Ref |
|---|------|-----|
| G1 | `pytest.ini` — lift from repo 2 (`asyncio_mode=auto`, markers `unit`, `integration`, `e2e`, default deselect `e2e`) | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.9 |
| G2 | `tests/conftest.py` — common fixtures: `db`, `s3`, `client`, `runner_mock` | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.9 |
| G3 | `tests/fixtures/mock_model_provider.py`, `mock_session_manager.py`, `mock_tools.py` — lift shape from repo 2 | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.9 |
| G4 | `tests/unit/test_repos.py` — basic CRUD on the 4 tables; `UNIQUE(session_id, object_key)` idempotency on artifacts | [`investigation.md`](investigation.md) §4 |
| G5 | `tests/unit/test_debounce.py` — verify 256-char / 250-ms flush behavior of the streaming-write helper | [`research/04-persistence-storage.md`](research/04-persistence-storage.md) §2.2 |
| G6 | `tests/unit/test_hmac.py` — HMAC verify / reject with 60-s clock skew | [`research/02-backend-agui.md`](research/02-backend-agui.md) §8 |
| G7 | `tests/integration/test_docker_runner.py` — real spawn + JSONL round-trip + warm-idle eviction + heartbeat-timeout terminate | [`research/03-runner-mgmt.md`](research/03-runner-mgmt.md) §2, [`architecture.md`](architecture.md) §3.3 |
| G8 | `tests/integration/test_sse_encoder.py` — verify wire format matches AG-UI 0.1.18 (`data: {...}\n\n`) | [`research/02-backend-agui.md`](research/02-backend-agui.md) §1 |
| G9 | `tests/integration/test_resume.py` — kill a runner mid-session, send new message, assert hydration + history continuity | [`architecture.md`](architecture.md) §3.4 |
| G10 | `tests/e2e/sse_client.py` — lift from repo 2; opt-in via `pytest -m e2e` | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.10 |
| G11 | `tests/e2e/test_vertical_slice.py` — the full PoC: send "Find handlers of OrderPlaced" → assert MCP call fires, sub-agent invoked, skill loaded, audit rows present, message persisted | [`investigation.md`](investigation.md) §6 |

**Exit criterion**: `pytest -m "not e2e"` is green in CI; `pytest -m e2e`
green locally against a running compose stack.

---

## Track H — Observability (light)

**Goal**: structured logs + OTel spans flowing somewhere we can read.

| # | Item | Ref |
|---|------|-----|
| H1 | Backend Dockerfile `CMD ["opentelemetry-instrument","uvicorn",…]` already in place (Track A7) | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.6 |
| H2 | `runner/__main__.py` boots `StrandsTelemetry().setup_console_exporter()` for dev; switch to `setup_otlp_exporter()` via env in prod | [`research/01-strands-minimal.md`](research/01-strands-minimal.md) §8 |
| H3 | Verify hook callbacks emit (or correlate with) OTel spans for tool calls — empirical check from R13 / open question | [`investigation.md`](investigation.md) §7 R13, §8 |
| H4 | Document OTLP env vars in `.env.example` (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_SERVICE_NAME=kloc-agent`) for when we wire a collector | [`research/05-reference-projects.md`](research/05-reference-projects.md) repo 2 §2.6 |
| H5 | (Deferred) Langfuse via OTLP — config-only swap, no code | [`investigation.md`](investigation.md) §2.2 |

**Exit criterion**: dev run emits at least one span per tool call to the
console; OTLP env vars are documented.

---

## Track dependency graph

```
A (infra)  ─┬─►  B (persistence)  ─┬─►  C (backend HTTP)  ──►  G (tests, ongoing)
            │                       │
            └─►  H (obs, light)     │
                                    │
                                    ├─►  D (runner + warm-idle)
                                    │       │
                                    │       └─►  E (skills)
                                    │
                                    └─►  F (frontend)
```

- **A is the only true prerequisite** — once A is done, B, F, and H can be
  worked in parallel.
- C blocks on B (needs the repos) and on D for end-to-end tests.
- D blocks on B (hydration reads from repos) and on the runner Dockerfile.
- E blocks on D (needs the agent + skills mount).
- F blocks on C (needs the backend's stream endpoint) but the scaffold (F1–F4)
  can be done against a fake backend in parallel.
- G is cross-cutting and grows alongside every other track.

---

## Milestones

| Milestone | What's runnable | Tracks complete |
|---|---|---|
| **M0 — compose up** | `docker compose up` lights up postgres + minio + backend `/healthz` | A |
| **M1 — schema + repos** | unit tests pass for the 4 tables | A, B |
| **M2 — backend SSE with fake runner** | `curl -N` against the stream endpoint yields SSE frames from a stub runner | A, B, C (sans real runner) |
| **M3 — real runner end-to-end** | a real container spawns, calls Anthropic, streams AG-UI events back | A, B, C, D |
| **M4 — warm-idle + rehydrate proven** | the integration test in G9 passes | A, B, C, D, partial G |
| **M5 — skills loadable** | demo skill `file_read`-ed mid-run | A, B, C, D, E |
| **M6 — PoC complete** | the vertical slice in `investigation.md` §6 runs in a browser | all tracks |

---

## How to use this plan with `writing-plans`

The `writing-plans` skill is for producing **detailed, code-aware**
implementation plans from a spec. This document is the spec input. To get
deeper plans for a single track, invoke:

> Use the `writing-plans` skill for Track D (Docker runner + warm-idle) of
> kloc-agent. The spec is `docs/implementation-plan.md` §"Track D" plus the
> referenced sections of `docs/architecture.md` §3.3 / §3.4 and
> `docs/research/03-runner-mgmt.md`.

This keeps the high-level checkbox plan stable while letting per-track
detail live in disposable plan files.
