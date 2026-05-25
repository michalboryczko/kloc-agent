# kloc-agent — Implementation Tasks

Demo-stability tasks for the runner ↔ backend transport seam plus the
tool-call policy layer. Each task is a separate file under `docs/tasks/`
and follows the USDL template:

```
# T<NN> — <title>
## Status                     — pending | in-progress | passed | blocked
## Spec references            — anchor ids in docs/usdl/*.xml + docs/specs/*.md
## Description                — what to build
## Deliverables               — file paths the task must produce
## How to review              — objective checks the verifier agent must run
## Dependencies               — prerequisite task ids
## Notes                      — open items, precedence ordering, cross-repo links
```

## Execution flow

For each task in dependency order (or in parallel batches per the
dependency graph):

1. **Task agent (`subagent_type: general-purpose` or `feature-implementer`).** Receives the task file content verbatim plus `docs/specs/<slug>.md` + the four `docs/usdl/*.xml` Sections as context. Implements the deliverables. Returns a short summary diff.
2. **Verifier agent (`subagent_type: code-reviewer` or `generalist-qa`).** Receives the task file's "How to review" section + the task agent's diff. Runs every numbered check inside the `<VERIFICATION>` block. Returns `pass | fail` + diagnostics.
3. **On `fail`:** re-spawn the task agent with the verifier's diagnostics appended. Cap at **3 retries** per task. Escalate to the human after the cap.
4. **Record status** in the per-task `## Status` line and in the table below.

The two agents see independent context — the verifier never reads the
task agent's reasoning, only the produced files plus the verification
checks. This matches the `inv.agent-context-isolation` discipline used
in the USDL plugin.

## Tasks

| ID  | Title                                                                                | Status  | Depends on  |
|-----|--------------------------------------------------------------------------------------|---------|-------------|
| T01 | fix-runner-communication: warm-runner reuse hang + oversized-frame channel poisoning | passed-infra-skipped | fix-runner-inbox (closed) |
| T02 | tool-result-size-limits: argument-aware tool policy + actionable hints               | passed-infra-skipped | —           |
| T03 | fix-runner-startup-heartbeat-race: first-heartbeat budget too tight for cold-start MCP init | passed-infra-skipped | —           |
| T04 | agents-autoregistry: load subagents from `agents/<name>/AGENT.md` (replaces hardcoded summarizer) | passed-infra-skipped | —           |
| T05 | tool-call parent linking: group tool calls under their assistant message in the UI   | passed               | —           |
| T06 | tool-call history persistence: restore tool calls after refresh                      | passed               | T05         |
| T07 | markdown rendering: render assistant prose as styled markdown, not raw text          | passed               | —           |

### Predecessor (closed, not numbered)

| Slug                | Title                                          | Status |
|---------------------|------------------------------------------------|--------|
| `fix-runner-inbox/` | PGMQ migration for runner inbox transport      | closed |

The closed `fix-runner-inbox/` lives as a directory under `docs/tasks/`
with its own multi-file structure (problem.md, root-cause.md, decision.md,
acceptance.md, implementation-plan.md, CHANGES.md, README.md, spec/).
That format predates the USDL single-file convention adopted here from
T01 onward. New tasks follow the single-file USDL template.

## Parallelisation hint

- **T01, T02, T03, and T04 are independent.** T02 ships a backend-side
  policy layer; T01 ships transport-layer fixes; T03 ships a
  heartbeat-dispatch fix plus a default-timeout bump; T04 ships a
  subagent autoregistry that replaces the hardcoded summarizer.
  Any can land first; all four can run concurrently against `master`.
- **T05 and T07 are independent of each other and of T01-T04.** T06
  depends on T05 (shared `parentMessageId` invariant on tool-call
  events). The recommended order is T05 → T06 sequentially, with T07
  landing in parallel with either. T07 is a pure-frontend change
  (markdown rendering) and has no cross-task file overlap with T05 /
  T06 on the frontend (T05 touches `frontend/src/lib/reducer.ts` and
  `types.ts`; T06 touches `reducer.ts:persistedToMessageView`,
  `types.ts`, and `lib/api.ts`; T07 touches `package.json`,
  `MarkdownContent.tsx` (new), and `AssistantBubble.tsx`).
- **File ownership across all seven tasks has minimal overlap.** T01
  owns `src/shared/`, `src/runner_mgmt/warm_idle.py`,
  `src/runner_mgmt/registry.py`, `src/db/models.py`, and the
  cap-rejection branch of `src/api/internal.py` (~lines 190+) plus
  the offload + permanent-failure paths in `runner/channel.py`. T02
  owns `src/hooks_audit/`, `runner/hooks/audit.py`, and adds fields
  to `src/settings.py`. T03 owns `src/runner_mgmt/heartbeat.py`,
  the `_heartbeat_loop` in `runner/channel.py`, the heartbeat
  dispatch branch of `src/api/internal.py` (~line 109), the
  `runner_heartbeat_timeout_s` default in `src/settings.py`, and
  `.env` / `.env.example`. T04 owns `runner/agents_loader.py` (new),
  the subagent construction block in `runner/agent_factory.py`, the
  `agents_dir` field on `HydrationPayload` and the
  `runner_subagent_load_failed` literal on `AuditEventType` in
  `src/db/models.py`, `build_agents_mount` in
  `src/runner_mgmt/hydrate.py`, the `Mounts` list in
  `src/runner_mgmt/docker_runner.py`, `agents/summarizer/AGENT.md`
  (new), the base prompt string in `src/api/stream.py`, and the
  `agents-init` sidecar + `kloc-agents` volume in `docker-compose*.yml`.
  The USDL Sections are the only shared write surface; each task's
  USDL changes touch disjoint Element ids so a merge is
  non-conflicting (T04 adds a new `cmp.runner.agents-loader` element
  and edits the `cmp.runner.entrypoint` structure block in a region
  T03 does not touch). T05 owns `runner/hooks/audit.py`,
  `runner/agent_factory.py` (AG-UI wrap region only),
  `src/api/webhooks.py` (tool-call.started branch),
  `src/streaming/normalize.py`, `frontend/src/lib/reducer.ts`
  (TOOL_CALL_START + ToolCallDenied handlers), and
  `frontend/src/lib/types.ts` (AG-UI event field). T06 owns a new
  `migrations/versions/*_tool_calls.py` migration, a new `ToolCall`
  ORM model in `src/db/models.py`, a new `src/repos/tool_calls.py`,
  the tool-call branches inside `src/api/stream.py:_persist_events`,
  the response-extension block inside `src/api/sessions.py:list_messages`,
  the `MessageOut` Pydantic schema, the `PersistedMessage` /
  `PersistedToolCall` types and `persistedToMessageView` in the
  frontend. T07 owns `frontend/package.json`, `frontend/package-lock.json`,
  the new `frontend/src/components/MarkdownContent.tsx`, and the
  message-content branch (lines ~52-57) of
  `frontend/src/components/AssistantBubble.tsx`. T05 / T06 share
  ownership of `frontend/src/lib/reducer.ts` but at distinct
  function scopes (T05 → reducer event handlers; T06 →
  `persistedToMessageView`) and `frontend/src/lib/types.ts` at
  distinct types (T05 → AG-UI event types; T06 → `PersistedMessage`
  / `PersistedToolCall`); merges are non-conflicting if landed
  sequentially.

## Standing verification conventions (apply to every task)

These conventions are written once here and referenced (not re-stated) by
every per-task `<VERIFICATION>` block from T05 onward. T01-T04 predate
the conventions; their verification blocks already inline the relevant
discipline.

- **Docker image rebuild is mandatory before any runtime verification.**
  Before running integration tests, Chrome MCP checks, or `curl` /
  `docker exec` probes against a running container, the verifier MUST
  rebuild the affected images and force-recreate the containers from the
  just-built images. The exact commands depend on which images the task
  touches; for a task affecting backend + runner + frontend the
  invocation is:
  ```bash
  docker compose build backend runner frontend
  docker compose up -d --force-recreate backend runner frontend
  docker compose ps   # verify "running" status with fresh image digest
  docker compose images backend runner frontend  # CREATED timestamp must be from THIS run
  ```
  A verifier that runs runtime checks against a stale cached image (or
  against a `docker compose up` that did not rebuild) MUST fail the task
  with diagnostics. The cost of one extra rebuild is dwarfed by the
  debugging cost of "the test passed but the deployed code is from
  yesterday".

- **UI verification uses Chrome MCP tools (`mcp__claude-in-chrome__*`).**
  Whenever a task changes frontend behaviour, the `<VERIFICATION>` block
  includes at least one Chrome MCP check that:
  1. Opens the freshly-rebuilt frontend container's URL via
     `mcp__claude-in-chrome__tabs_create_mcp` / `navigate`.
  2. Drives the UI through the change-under-test (sending a prompt,
     reloading a session, etc.).
  3. Asserts on the rendered DOM via `mcp__claude-in-chrome__find` +
     `mcp__claude-in-chrome__javascript_tool` (with stable
     `data-test="..."` selectors — never on Tailwind class strings).
  4. Captures a `mcp__claude-in-chrome__gif_creator` recording of the
     interaction, attached to the task closure note for human review.
  5. Reads `mcp__claude-in-chrome__read_console_messages` and asserts
     the absence of any console errors / regression warnings the task
     intentionally introduces (e.g. the strict-drop console.warn from
     T05's reducer).
  Unit tests, integration tests, and `curl`-based API checks remain the
  primary correctness signal for backend behaviour; Chrome MCP exists
  specifically to verify rendered UI semantics that DOM-snapshot tests
  cannot reliably capture (visual grouping, streaming behaviour, markdown
  output, etc.).

- **Cross-task verification re-runs.** A task that touches the frontend
  rendering surface MUST, as its final verification step, re-run the
  Chrome MCP checks from any other shipped UI-touching task to confirm
  no regression. E.g. T07 (markdown) re-runs T05's grouping check and
  T06's history-reload check against the T07 frontend image. This
  applies to UI tasks only; backend-only tasks do not need to re-run
  UI checks.

## Spec-amendment policy

Unlike the USDL MVP plugin (which uses overlay bundles via `usdl merge`
from T29 onward), kloc-agent does not have an overlay merge tool. Each
task amends `docs/usdl/{behavior,topology,interfaces,composition}.xml`
in place as part of its deliverables. The verifier checks confirm the
amendments landed via `grep -F` against the canonical files.

## Cross-repo dependencies

T02 has one cross-repo deliverable: a `/v1/file_stat` HTTP endpoint
in the `kloc-intelligence` repo. That sibling change is tracked
separately. A fixture stat server (`tests/fixtures/intel_stat_server.py`)
is the contract source of truth in this repo until the real endpoint
lands.

T03 has no cross-repo dependencies.

T04 has no cross-repo dependencies. The `agents/` directory and
`kloc-agents` named volume are local to this repo.

T05, T06, and T07 have no cross-repo dependencies. All three are
self-contained within this repo (runner + backend + frontend +
migrations + docs).

## Open Items roll-up

PM decisions blocking task closure are listed in each task's
`## Notes` section. The roll-up below names which task each open item
belongs to:

- **T01:** 429-status classification (permanent vs transient);
  terminal-frame `RUN_ERROR` synthesis policy; artifact MIME for
  offloaded AG-UI payloads; whether AC1's `< 200 ms` enqueue bound
  becomes an `<nfr>` under `beh.ask-assistant`.
- **T02:** `file_stat` MCP-tool exposure (in addition to the private
  HTTP endpoint); ship-with-defaults vs ship-empty for
  `KLOC_TOOL_LIMITS`; hint copy review; whether to add a `<rule>`
  under `beh.ask-assistant` for "tool denials carry an actionable
  hint the agent observes as the tool result"; audit payload schema
  versioning for the new `tool_limit:*` reason namespace.
- **T03:** whether to tighten the 60 s default once
  `kloc_agent.runner.spawn_to_first_beat_s` telemetry stabilises;
  whether to extend the first-beat frame with structured
  `mcp_init_ms` / `agent_build_ms` timings so operators can drill
  into cold-start regressions; whether the `INJECT_RUNNER_INIT_DELAY_S`
  test fixture should remain in the runner image after T03 closes
  or move into a test-only Dockerfile overlay.
- **T04:** per-subagent tool filtering via frontmatter `tools:`
  allowlist (deferred until a specialist demonstrably suffers from
  the full 22-tool MCP surface); per-subagent model override via
  frontmatter `model_id:` (deferred until cost telemetry justifies);
  whether subagent intermediate events propagate through the
  orchestrator's AG-UI stream (empirical check on a non-summarizer
  subagent); migration path to Strands `graph` orchestration once
  ≥3 deterministically-routed subagents land.
- **T05:** Strands hook surface — does `MessageStartEvent` /
  `MessageStopEvent` fire synchronously before AG-UI emission, or do
  we need to wrap `agui_emit` with a closure that snoops
  `TEXT_MESSAGE_START` / `TEXT_MESSAGE_END`? Verify against
  `docs/research/02-backend-agui.md` before implementation.
  Webhook-payload schema versioning for the new optional
  `parent_message_id` field (follows T02's "additive, no version
  bump" precedent unless a versioning policy lands first).
- **T06:** result-size persistence policy — truncate to
  `KLOC_TOOL_LIMITS.result_max_bytes` in the DB row (chosen) vs.
  offload to an artifact row and store a pointer (deferred); `seq`
  allocation race within a parent message (chosen: tolerate
  duplicate seq, tie-break by created_at; alternative: advisory
  lock); whether to introduce a frontend unit-test runner
  (vitest + react-testing-library) as a sibling task so future UI
  changes have a faster signal than Chrome MCP.
- **T07:** syntax highlighting via `shiki` (deferred until UX
  research justifies the bundle cost); whether to ship
  `isomorphic-dompurify` as a safety lever even without importing it
  (chosen yes; revisit if bundle-size pressure increases); whether
  user-bubble (`AnalystBubble.tsx`) should also render markdown
  (deferred until analysts ask for it); memoisation of the markdown
  render across streamed deltas (defer; profile first).

None of the open items block the verification checks listed in each
task's `<VERIFICATION>` block. They surface during the task's review
cycle and either resolve before close or land as follow-ups.
