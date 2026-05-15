# Feature: kloc-agent (PoC)

> Distilled spec for the kloc-agent PoC. The authoritative design lives in
> [`docs/investigation.md`](../investigation.md); this file extracts
> testable acceptance criteria QA can verify. No implementation details —
> those belong in the architect's plan (`kloc-agent-poc-plan.md`).

---

## Goal

`kloc-agent` is a self-hosted research-agent service over the existing
`kloc-intelligence` MCP. The user is **an analyst, not an engineer**: they
open a web chat, ask a natural-language question about a PHP code base, and
receive a streamed, sourced answer without ever touching a CLI. The PoC must
prove the full vertical slice — session lifecycle, streamed reasoning, at
least one MCP tool call, at least one sub-agent delegation, at least one
skill loaded via progressive disclosure, hook-driven audit, durable
persistence, and warm-idle eviction with no-seam rehydrate — running
end-to-end in a browser against the locked stack (Python 3.12 + FastAPI +
SQLAlchemy 2.0 async + Strands Agents 1.39.0 + per-session Docker runners
via aiodocker + Postgres + MinIO + Next.js 16 + CopilotKit 1.52.1 + AG-UI
0.1.18 over SSE).

---

## Scope

From [`investigation.md`](../investigation.md) §6 (vertical PoC slice):

- A single hardcoded analyst can open the chat at `https://kloc-agent.local`
  and start a fresh session via `POST /v1/sessions`.
- Sending a message via `POST /v1/sessions/{id}/stream` (with a
  CopilotKit `RunAgentInput` envelope) drives the agent end-to-end.
- The backend persists the user message **before** forwarding it to the
  runner; the response streams back over SSE as AG-UI events.
- A per-session Docker runner container hosts the Strands `Agent` loop,
  connects to `kloc-intelligence` over stdio MCP, loads skills from a
  read-only bind-mounted `./skills/` directory, and exposes a `summarizer`
  sub-agent via the agents-as-tools pattern.
- Hook-driven audit: `BeforeToolCallEvent` POSTs an HMAC-signed webhook to
  the backend for every tool call (including sub-agent delegation). Audit
  rows are persisted **before** the webhook returns `{decision: "allow"}`.
- Warm-idle eviction: after `RUN_FINISHED`, a per-session
  `WarmIdleManager` terminates the container after
  `RUNNER_WARM_IDLE_S=60` seconds of silence. A new message inside the
  window reuses the same container.
- Same-chat rehydrate: a follow-up after the warm-idle window spawns a
  fresh container; the backend writes a `HydrationPayload` with full
  message history + last `STATE_SNAPSHOT` to a tempfile, bind-mounts it
  read-only at `KLOC_HYDRATION_PATH=/run/kloc/hydration.json`, and the
  Strands adapter rebuilds the agent's internal history from
  `RunAgentInput.messages`. The LLM sees no seam.
- Resume after browser disconnect: the backend's `ExecutionRegistry`
  buffers events keyed by `(session_id, run_id)`; reconnecting with
  `last_event_id` replays the tail and continues live.
- Crash recovery: a heartbeat watcher
  (`RUNNER_HEARTBEAT_TIMEOUT_S=30`) marks the session `crashed`;
  partial tool calls land in audit as `tool_call.crashed` and the
  analyst is surfaced "session interrupted, click to retry."
- Concurrent sessions: each `session_id` runs in its own container;
  there is no cross-session state.
- Artifact upload plumbing exists end-to-end (MinIO bucket via
  `mc-init` sidecar, `artifact_metadata` table with
  `UNIQUE(session_id, object_key)`, presigned `GET /v1/artifacts/{id}`).

---

## Out of Scope

From [`investigation.md`](../investigation.md) §2.2 and §6 ("does not
include"):

- AWS Bedrock AgentCore runner (`DockerRunner` is the only mode).
- Multi-tenant auth; a single hardcoded analyst identity.
- Langfuse exporter (OTel console / OTLP only).
- Lifecycle / retention rules on artifacts (PoC artifacts live forever).
- Graph- / swarm-style multi-agent topology (agents-as-tools is enough).
- Branching messages in the UI (`messages.parent_message_id` is in the
  schema but unused).
- Soft delete vs hard delete.
- Token-window summarization for hydration (runner trusts the payload).
- Policy enforcement in hooks beyond audit (`Policy.decide` is a noop
  that always returns `allow`).
- Image-prewarm / warm-spare containers and process-pool runners.
- Frontend tool actions beyond default `useRenderToolCall`.
- Multi-replica backend (single replica; in-process `RunnerRegistry`).

---

## Critical Constraints

These five must hold in every implementation and test path. They are
load-bearing for the warm-idle + rehydrate story
([`docs/architecture.md`](../architecture.md) §3.3, §3.4).

1. **Single chat = single `session_id`. Runner container is warm-idle for
   60 s between messages, then terminated. The next message respawns a
   fresh container and rehydrates the full history from Postgres — the
   analyst sees no seam.** Postgres + MinIO + the `skills/` mount is the
   entire durable surface; the runner is stateless.
   ([`architecture.md`](../architecture.md) §3.3, §3.4;
   [`investigation.md`](../investigation.md) §2.1 "session manager".)
2. **Backend NEVER runs the agent loop or calls the model.** The backend
   persists, orchestrates, spawns containers, and proxies AG-UI events.
   The Strands `Agent` loop, the model client, and the MCP client all
   live inside the per-session Docker container.
   ([`investigation.md`](../investigation.md) §1, §3.)
3. **Strands hooks are IN-PROCESS only.** The webhook layer wraps
   `httpx.post(...)` inside the hook callback —
   `BeforeToolCallEvent` POSTs `/v1/webhooks/runners/{rid}/events` from
   inside the runner's Python process; there is no SDK-native webhook
   dispatcher to fall back on.
   ([`investigation.md`](../investigation.md) §2.1 "hook pattern".)
4. **Strands silently defaults to Bedrock** when no `model=` is passed
   and will fail without AWS creds. Always construct the model
   explicitly — `AnthropicModel(...)` on dev — and route through a
   `LLM_PROVIDER` env switch in `runner/model_factory.py`.
   ([`investigation.md`](../investigation.md) §2.1, §7 R2.)
5. **`strands_agentskills` is NOT on PyPI.** It must be installed from
   git pinned to a specific commit hash
   (`agentskills @ git+https://github.com/aws-samples/sample-strands-agents-agentskills@<sha>`)
   so builds are reproducible. Vendoring is the escape hatch if upstream
   API churns. ([`investigation.md`](../investigation.md) §7 R1.)

---

## Acceptance Criteria

GIVEN/WHEN/THEN bullets, each annotated with the milestone it belongs to
(M0–M6 in [`implementation-plan.md`](../implementation-plan.md) §
Milestones) and one of the 12 QA scenarios listed at the bottom of this
section. Criteria are testable end-to-end against a running compose
stack; non-functional constraints (image versions, lock-file pins) are
verified via Track G unit + integration tests.

### Session lifecycle

- **AC1.** GIVEN no session exists for the hardcoded analyst, WHEN the
  frontend issues `POST /v1/sessions`, THEN the backend persists a
  `sessions` row (`analyst_id` set, `closed_at IS NULL`), writes an
  `audit_log` row `event_type=session_opened`, and returns `201
  {session_id, created_at}`. *(M0 → M2, QA1 — session lifecycle.)*
- **AC2.** GIVEN an open session with at least one message, WHEN the
  frontend issues `GET /v1/sessions/{id}` and `GET
  /v1/sessions/{id}/messages?after=cursor&limit=100`, THEN the backend
  returns session metadata (including `runner_state`) and the full
  ordered message history paginated by per-session monotonic `seq`.
  *(M2, QA1 — session lifecycle.)*
- **AC3.** GIVEN an open session, WHEN the analyst (or scheduler) issues
  `POST /v1/sessions/{id}/close`, THEN `closed_at` is set on the
  `sessions` row and subsequent `POST /v1/sessions/{id}/stream` calls
  reject with 4xx. *(M2, QA1 — session lifecycle.)*

### Message streaming

- **AC4.** GIVEN an open session, WHEN the frontend `POST`s a
  `RunAgentInput` to `/v1/sessions/{id}/stream` with
  `Accept: text/event-stream`, THEN the backend
  (a) persists the user `Message` row **before** forwarding to the
  runner, (b) returns a `StreamingResponse` whose body is a sequence
  of well-formed AG-UI 0.1.18 SSE frames (`data: {...}\n\n`), and
  (c) every event yielded over SSE is also persisted (text deltas via
  the batched 256-char / 250-ms debounce, then finalized by
  `TEXT_MESSAGE_END`). *(M2 → M3, QA2 — message streaming.)*
- **AC5.** GIVEN a stream is in flight, WHEN the analyst's browser
  disconnects, THEN `request.is_disconnected()` causes the SSE
  generator to break, the runner continues to completion, and every
  buffered event remains available via the `ExecutionRegistry` for
  cursor-replay. *(M3, QA9 — resume after disconnect.)*

### MCP tool invocation

- **AC6.** GIVEN the runner is RUNNING with `kloc-intelligence` spawned
  as a stdio MCP child, WHEN the LLM emits a tool call against a
  `kloc_*` MCP tool, THEN the runner forwards the JSON-RPC 2.0 call
  over stdio, the result is streamed back as `TOOL_CALL_START` →
  `TOOL_CALL_ARGS` → `TOOL_CALL_END` → `TOOL_CALL_RESULT` AG-UI events,
  and the result lands in the persistent `Message` history as a
  `tool` role row with `content_parts` populated. *(M3, QA3 — MCP tool
  invocation.)*

### Sub-agent delegation

- **AC7.** GIVEN the orchestrator has a `summarizer` sub-agent wired
  via the agents-as-tools pattern (`tools=[..., summarizer_agent]`),
  WHEN the orchestrator delegates to it, THEN the call routes through
  the same `BeforeToolCallEvent` hook lifecycle as any other tool
  (audit row written, webhook decision honored), and the sub-agent's
  output is captured as the tool call result. *(M3, QA4 — sub-agent
  delegation.)*

### Skill loading

- **AC8.** GIVEN `./skills/<demo-skill>/SKILL.md` is present and the
  directory is bind-mounted read-only at `/skills` in the runner
  container, WHEN the runner boots, THEN `discover_skills(Path("/skills"))`
  enumerates the skill, `generate_skills_prompt(skills)` is appended to
  the `Agent.system_prompt`, and the `file_read` tool is registered so
  the LLM can progressively load the SKILL.md body on demand. *(M5,
  QA5 — skill loading.)*
- **AC9.** GIVEN a question that matches the demo skill's description,
  WHEN the LLM runs, THEN it issues a `file_read` against the SKILL.md
  body at least once during the run (verified via audit / OTel trace).
  *(M5, QA5 — skill loading.)*

### Audit webhook (hook-driven)

- **AC10.** GIVEN any tool call is about to fire in the runner, WHEN
  the `BeforeToolCallEvent` callback runs, THEN it POSTs
  `/v1/webhooks/runners/{rid}/events` with
  HMAC-SHA256(`timestamp + body`) authorization, the backend
  **persists the `audit_log` row before** returning, and responds
  `202 {decision: "allow"}` (PoC policy is allow-all). *(M3, QA6 —
  audit webhook.)*
- **AC11.** GIVEN the backend webhook receiver, WHEN it receives a
  request older than 60 s or with an invalid HMAC, THEN it rejects
  with 4xx and writes no audit row. *(M3, QA6 — audit webhook.)*
- **AC12.** GIVEN the backend is slow to respond, WHEN the `Before*`
  hook webhook exceeds a 2 s deadline, THEN the hook denies the tool
  call by default and surfaces this as a `HookBackpressure`
  `CustomEvent` in the AG-UI stream. *(M3, QA6 — audit webhook.)*

### Warm-idle + eviction

- **AC13.** GIVEN a runner has just emitted `RUN_FINISHED` for a
  session, WHEN no new user message arrives within
  `RUNNER_WARM_IDLE_S=60` seconds, THEN the per-session
  `WarmIdleManager` calls `DockerRunner.terminate(handle,
  graceful_timeout=5)`, the container is stopped and removed, and
  `audit_log` records `runner_warm_idle_evicted`. *(M4, QA7 — warm-idle
  + eviction.)*
- **AC14.** GIVEN a runner is in WARM-IDLE state, WHEN a new user
  message arrives before the timer expires, THEN the timer is
  cancelled (via `asyncio.Task.cancel()`), the same container handles
  the follow-up message, and no respawn happens. *(M4, QA7 — warm-idle
  + eviction.)*
- **AC15.** GIVEN a warm-idle kill is mid-flight (`container.stop`
  in progress), WHEN a new user message races into
  `WarmIdleManager.on_user_message()`, THEN the manager awaits the
  in-flight kill task before deciding spawn-vs-reuse and falls
  through to fresh-spawn-with-hydration if the container has already
  terminated (treated as a normal cold start). *(M4, QA7 — warm-idle
  + eviction; risk [`investigation.md`](../investigation.md) §7 R6b.)*

### Rehydrate (same-chat resume)

- **AC16.** GIVEN a session whose container was warm-idle-evicted,
  WHEN a new user message arrives, THEN the backend (a) reads the
  full message history from `messages` + the last `STATE_SNAPSHOT`
  from `audit_log`, (b) writes a `HydrationPayload` JSON file to
  `/tmp/hydration-<rid>.json`, (c) `DockerRunner.spawn` bind-mounts
  it read-only at `KLOC_HYDRATION_PATH=/run/kloc/hydration.json`,
  and (d) `ag_ui_strands.StrandsAgent.run(RunAgentInput)` rebuilds
  the Strands `Agent.messages` from `RunAgentInput.messages` before
  the first `stream_async` call. *(M4, QA8 — rehydrate same-chat.)*
- **AC17.** GIVEN a rehydrated session, WHEN the LLM responds, THEN
  it references prior turns correctly (test asserts the model's
  reply mentions information that only appears in the prior
  history), and the analyst-observable cold-start latency is
  ≤ 2 s before the first token streams. *(M4, QA8 — rehydrate
  same-chat;
  [`architecture.md`](../architecture.md) §3.4 cold-start budget.)*

### Resume after disconnect

- **AC18.** GIVEN a run that completed (or is in flight) after the
  analyst's browser disconnected, WHEN the browser reconnects via
  `GET /v1/sessions/{id}/stream?run_id=...&last_event_id=...`, THEN
  the `ExecutionRegistry` replays buffered events after the cursor
  and continues live until `RUN_FINISHED`. *(M3 → M4, QA9 — resume
  after disconnect.)*

### Hook deny path

- **AC19.** GIVEN the backend's `Policy.decide` is swapped to return
  `{decision: "deny", reason: "..."}` for a specific tool, WHEN the
  LLM emits that tool call, THEN the runner's
  `BeforeToolCallEvent` callback sets `event.cancel_tool = "<reason>"`,
  the tool does not execute, and the analyst sees a `TOOL_CALL_RESULT`
  carrying the denial. *(M3, QA10 — hook deny path.)*

### Runner crash recovery

- **AC20.** GIVEN a runner has stopped emitting heartbeats, WHEN
  `RUNNER_HEARTBEAT_TIMEOUT_S=30` seconds elapse without one, THEN
  the heartbeat watcher terminates the container, the
  `sessions.runner_state` becomes `crashed`, `audit_log` records
  `runner_heartbeat_lost`, and any in-flight tool call has a
  `tool_call.crashed` audit row. No auto-restart. *(M4, QA11 — runner
  crash recovery.)*
- **AC21.** GIVEN a crashed runner, WHEN the analyst sends a new
  message, THEN the backend treats it as a same-chat rehydrate
  (AC16 path) and the analyst is informed that the prior in-flight
  tool call was lost. *(M4, QA11 — runner crash recovery.)*

### Concurrent sessions

- **AC22.** GIVEN two open sessions for the same analyst, WHEN both
  receive user messages concurrently, THEN two distinct Docker
  containers are spawned (one per `session_id`), each emits its own
  AG-UI event stream, and there is no cross-session contamination in
  audit, messages, or `ExecutionRegistry` buffers. *(M3 → M4, QA12 —
  concurrent sessions.)*

### Artifact upload

- **AC23.** GIVEN the MinIO bucket has been created by the `mc-init`
  sidecar at compose-up, WHEN the runner uploads an artifact (or a
  test fixture simulates one) to
  `sessions/{session_id}/artifacts/{artifact_id}/{filename}` and
  the backend webhook registers it, THEN an `artifact_metadata` row
  is created with `UNIQUE(session_id, object_key)` idempotency, and
  `GET /v1/artifacts/{id}` returns a 302 to a presigned MinIO URL.
  *(M4 → M6, QA13 — artifact upload.)*

### Boot-time recovery (cross-cutting)

- **AC24.** GIVEN the backend restarts while messages have
  `finalized_at IS NULL`, WHEN it boots, THEN it scans the orphaned
  set and writes `audit_log.event_type = 'stream_orphaned'` for each.
  *(M1, QA1 — session lifecycle.)*
- **AC25.** GIVEN orphan containers exist (labelled
  `kloc.role=runner`) from a previous backend run, WHEN the backend
  boots, THEN the boot-time sweeper kills + removes them before
  serving requests.
  *(M3, QA11 — runner crash recovery;
  [`implementation-plan.md`](../implementation-plan.md) Track D §D9.)*

### Build / dependency pinning

- **AC26.** GIVEN the system is deployed, WHEN any environment is
  built, THEN `ag-ui-protocol` is pinned to exactly `0.1.18` in the
  Python `pyproject.toml` AND `@ag-ui/client` is pinned to exactly
  `0.0.42` in `frontend/package.json`. Drift between these two
  versions breaks SSE event-shape compatibility and is a
  release-blocker.
  *(M0, QA14 — build / dependency pinning;
  [`investigation.md`](../investigation.md) §7 R5.)*

### QA scenario map

The 12 (functional) + 1 (cross-cutting artifact) QA scenarios above
group the criteria. Each scenario must be exercised at least once,
either as an integration test (`tests/integration/`) or as an
opt-in e2e test (`tests/e2e/`, `pytest -m e2e`):

| QA# | Scenario | Primary ACs |
|---|---|---|
| QA1 | Session lifecycle (create / get / list / close, boot recovery) | AC1, AC2, AC3, AC24 |
| QA2 | Message streaming over SSE with persist-then-yield | AC4 |
| QA3 | MCP tool invocation (`kloc_*` stdio JSON-RPC) | AC6 |
| QA4 | Sub-agent delegation via agents-as-tools | AC7 |
| QA5 | Skill loading + progressive disclosure | AC8, AC9 |
| QA6 | Audit webhook (HMAC, persist-before-decide, deadline) | AC10, AC11, AC12 |
| QA7 | Warm-idle + eviction (60 s timer, race) | AC13, AC14, AC15 |
| QA8 | Same-chat rehydrate after eviction | AC16, AC17 |
| QA9 | Resume after browser disconnect (`last_event_id` replay) | AC5, AC18 |
| QA10 | Hook deny path | AC19 |
| QA11 | Runner crash recovery (heartbeat-dead, orphan sweep) | AC20, AC21, AC25 |
| QA12 | Concurrent sessions (per-`session_id` container isolation) | AC22 |
| QA13 | Artifact upload + presigned download | AC23 |
| QA14 | Build / dependency pinning (`ag-ui` pair) | AC26 |

---

## Links

- [`docs/poc.md`](../poc.md) — original project brief; success criteria.
- [`docs/investigation.md`](../investigation.md) — locked-decisions
  source spec. §1 system shape, §2.1 stack picks, §2.2 deferred items,
  §3 module layout, §4 DB schema, §5 REST/SSE shape, §6 vertical PoC
  slice, §7 risk inventory, §8 open questions.
- [`docs/architecture.md`](../architecture.md) — three ASCII diagrams
  (system, backend internals, runner internals). §3.3 warm-idle state
  machine, §3.4 rehydrate flow + cold-start budget.
- [`docs/implementation-plan.md`](../implementation-plan.md) —
  checkbox plan, Tracks A→H, M0→M6 milestones, dependency graph.
- [`docs/research/01-strands-minimal.md`](../research/01-strands-minimal.md)
  — Strands SDK minimal usage; hooks, sub-agents, skills.
- [`docs/research/02-backend-agui.md`](../research/02-backend-agui.md)
  — FastAPI + AG-UI SSE; event taxonomy; persistence invariants.
- [`docs/research/03-runner-mgmt.md`](../research/03-runner-mgmt.md)
  — Docker runner; aiodocker; warm-idle implementation notes.
- [`docs/research/04-persistence-storage.md`](../research/04-persistence-storage.md)
  — Postgres schema; Alembic; MinIO; debounce strategy.
- [`docs/research/05-reference-projects.md`](../research/05-reference-projects.md)
  — `aws-samples/*` patterns lifted into `kloc-agent`.
