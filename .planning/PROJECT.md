# kloc-agent

## What This Is

`kloc-agent` is a single-operator AI agent orchestration system: a FastAPI backend spawns disposable Docker runner containers per session, streams Strands-Agents events back over AG-UI to a Next.js/CopilotKit browser UI, and persists every message + audit event to Postgres. Runners connect to an external `kloc-intelligence` MCP server for the actual codebase-knowledge tools. The product today is a PoC for a single analyst exploring an indexed PHP codebase; this milestone hardens it to demo-stable for internal beta use.

## Core Value

A single analyst can have a live, resumable, audit-complete agent conversation against an indexed codebase — and trust that every event, message, and tool call is reliably persisted, ordered correctly, and never silently dropped.

## Requirements

### Validated

<!-- Existing capabilities, inferred from .planning/codebase/ and verified in the working tree. -->

- ✓ Session CRUD over REST (`POST /v1/sessions`, list, get, close) — existing
- ✓ AG-UI streaming POST + cursor-replay GET on `/v1/sessions/{id}/stream` — existing
- ✓ Per-session disposable Docker runner via `DockerRunner` + `RunnerRegistry` — existing
- ✓ Warm-idle eviction + heartbeat watcher for runner containers — existing
- ✓ Runner→backend JSONL ingress and backend→runner long-poll inbox — existing
- ✓ HMAC-signed tool-call webhooks (`BeforeToolCall`, `AfterToolCall`, `ArtifactRegistered`) — existing
- ✓ Audit log with locked 12-event vocabulary in Postgres — existing
- ✓ MinIO/S3 artifact storage — existing
- ✓ Strands-Agents + MCP client wiring inside the runner — existing
- ✓ Next.js/CopilotKit frontend with session picker, agent-proxy, AG-UI HttpAgent — existing
- ✓ OpenTelemetry auto-instrumentation across backend + runner — existing
- ✓ Alembic schema with initial migration — existing

### Active

<!-- Hardening scope. Source: docs/reviews/code-review/, docs/reviews/ui-design-review/, docs/reviews/frontend/. -->

**Backend correctness (from `docs/reviews/code-review/issues.md`):**

- [ ] **ISS-01** — Publish `RUN_STARTED` before flushing the pre-`RUN_STARTED` orphan buffer (`src/api/internal.py`); add ordering-invariant unit test
- [ ] **ISS-02** — Key persister tasks by `(session_id, run_id)`; reconnect must not double-subscribe the event bus; add reconnect double-spawn test
- [ ] **ISS-03** — `AuditHookSender.stop()` drains `_after_queue` before cancelling; add post-shutdown audit-row-count test
- [ ] **ISS-04** — Compare-and-swap pop on `RUN_FINISHED`/`RUN_ERROR` in `src/api/internal.py`; only pop when `active_by_session[session_id] == run_id`
- [ ] **ISS-05** — Promote `llm_model_id` to `Settings`; remove raw `os.environ.get("LLM_PROVIDER"/"LLM_MODEL_ID")` reads from `src/api/stream.py`
- [ ] **ISS-06** — Track last yielded frame in `runner/channel.py` and replay on reconnect; covers transport-loss `RUN_FINISHED` symptom

**Backend hardening + cleanup (from `docs/reviews/code-review/issues.md`):**

- [ ] **ISS-07** — Validator rejecting `kloc_hook_secret == "dev-secret-please-rotate"` when `allow_hmac_fallback=True` and not stub mode
- [ ] **ISS-08** — Gate `_diag` stderr writes behind `Settings.diag_events` (or `KLOC_DIAG` env), default off
- [ ] **ISS-09** — Remove annotated assignments on `app.state.*` (or migrate to an `AppState` dataclass)
- [ ] **ISS-10** — Either cache `is_alive` with ~50 ms TTL or accept the minor thundering-herd cost (decide during planning)
- [ ] **ISS-11** — `ClientDisconnect` response in `src/api/internal.py` distinguishes "no bytes" from "some frames then disconnect"
- [ ] **ISS-12** — Remove `kloc_runner_mode` setting; unconditionally construct `DockerRunner` at boot; fail loudly on `ImportError`; clean up `.env.example` and tests using `KLOC_RUNNER_MODE=stub`
- [ ] **ISS-13** — Mechanical comment sweep across the 35 files identified (~161 offending comments); land as a single behaviour-neutral PR; add the comment policy to `CLAUDE.md` (or `CONTRIBUTING.md`)

**UI design overhaul (from `docs/reviews/ui-design-review/`):**

- [ ] **UI-P0** — Lock the four design decisions: styling stack (Tailwind v4 vs CSS Modules), shadcn/ui adoption, dark-only vs dark+light, font loading via `next/font` vs `@import`
- [ ] **UI-P1** — Foundations: install styling stack, replace `globals.css` with token theme, load display + body + mono fonts, atmosphere background, CopilotKit CSS variable overrides
- [ ] **UI-P2** — Component reskin: `ToolCallCard`, `AgentBody`, extract `SessionPicker` from `page.tsx`, chat-view header + grid in `page.tsx`
- [ ] **UI-P3** — Replace `<CopilotSidebar>` with inline `<CopilotChat>` + new `SessionRail.tsx`; this is the structural fix for the "75% black space" complaint (F-1)
- [ ] **UI-P4** — Polish: page entrance animation, hover-shift session rows, runner-state dot glow, streaming indicator, tool-card chevron animation, `<title>`/`<meta>` polish
- [ ] **UI-P5** — Accessibility audit: textarea name (A-1), `--text-mute`/`--text-dim` tokens (A-2), back-button `aria-label` (A-3), session row `aria-busy` (A-4), heading hierarchy (A-5)
- [ ] **UI-P6** — Delete dead UI code: `ChatWindow.tsx`, `Composer.tsx`, `lib/agui-http-agent.ts`, `utils/sseParser.ts`

**Frontend code-quality (from `docs/reviews/frontend/`):**

- [ ] **FE-PERF** — Memoize/hoist inline objects and stabilise hooks (7 findings in `performance.md`)
- [ ] **FE-BUNDLE** — Hoist CopilotRuntime out of the per-request POST handler in `api/copilotkit/route.ts`; lazy-load CopilotKit on the chat page so the picker doesn't ship the chat bundle (4 findings in `bundle-and-loading.md`)
- [ ] **FE-ROUTES** — Server / route hardening on `agent-proxy` and `copilotkit` routes: SSE lifecycle, request scope, runtime allocation (5 findings in `server-and-routes.md`)
- [ ] **FE-DATA** — Fetch lifecycle for `listSessions`/`listMessages`: dedup, abort, error surface (4 findings in `data-fetching.md`)
- [ ] **FE-SEC** — CSRF posture, console-warn body dump behind debug flag, input trust on `agent-proxy` (4 findings in `security.md`)
- [ ] **FE-QUALITY** — Dead code removal (overlaps UI-P6), DRY, type widening tightening on `IncomingBody` (7 findings in `code-quality.md`)

### Out of Scope

- **Authentication / authorisation on `/v1/*` and `/internal/*` endpoints** — Demo-stable target is single-operator on the compose stack with network isolation; multi-tenant auth defers to the next milestone
- **Replacing the hardcoded `HARDCODED_ANALYST_ID = "analyst-poc"`** — same reason as above
- **Rate limiting on `POST /v1/sessions/{id}/stream`** — single-operator surface; rate-limit defers with auth
- **Horizontal scaling / multi-worker uvicorn** — single-worker is intentional for this milestone; in-process state (event bus, execution registry, active-run dict) stays as-is
- **Vendoring or replacing `strands_agentskills` git-SHA pin** — accepted risk for now; tracked but not in milestone
- **Findings outside the three referenced review directories** — `docs/reviews/test-failures-root-cause-mapping.md` and `docs/reviews/unmapped-findings.md` items (including the HMAC sign/verify divergence on non-ASCII bodies and the `EventBus.publish` `QueueFull`-swallow) are deferred; they will be a follow-up milestone
- **New product features** — strictly stabilisation; nothing new ships in this milestone
- **Real-time chat, video posts, mobile, OAuth** — n/a to product class
- **`ExecutionRegistry.gc()` lifespan wiring (CONCERNS memory-leak item)** — flagged but deferred unless it surfaces in beta usage
- **`MessageRepo._next_seq` PostgreSQL-sequence rewrite** — accepted as-is for single-operator demo load; full rewrite deferred

## Context

- The PoC has been actively reviewed — three thorough review passes exist (backend correctness, UI design, frontend code quality) totalling ~50+ documented findings with file:line evidence
- Most of the open backend bugs cluster around AG-UI event ordering (`src/api/internal.py`) and runner lifecycle (`src/runner_mgmt/registry.py`) — both flagged as "fragile areas" in `.planning/codebase/CONCERNS.md`
- The frontend works but visually reads as "default browser styles + inline tweaks" — the chat view has a known structural issue where `<CopilotSidebar>` is overlaid on an essentially empty page
- Recent commit `13fd93f57 WIP: kloc-agent-poc - baseline before fix sprint` was deliberately created as the starting line for this milestone
- The codebase map at `.planning/codebase/` (refreshed 2026-05-15) is the canonical architectural reference; planning + executing agents should read it before touching files
- Tests live at `tests/unit/`, `tests/integration/`, and a vertical-slice suite; new tests for ISS-01..06 land alongside their fixes
- Runtime is `claude` and GSD's required subagents are **not currently installed** at `/Users/michal/.claude/agents/` — installer (`npx get-shit-done-cc@latest --global`) must be run before planning agents are usable; otherwise plan/execute phases will need to operate inline

## Constraints

- **Tech stack (backend)**: Python 3.12, FastAPI ≥ 0.115, SQLAlchemy 2.x async + asyncpg, `strands-agents==1.39.0`, `ag-ui-protocol==0.1.18`, `ag_ui_strands==0.1.8`, uv 0.5.4 — locked, no version upgrades in this milestone
- **Tech stack (frontend)**: Next.js 16.0.8, React 19.2.1, CopilotKit 1.56.5, `@ag-ui/client 0.0.42`, TypeScript 5.6 strict — locked; UI work uses what ships with these versions
- **Runtime mode**: Docker is the only runner mode after ISS-12; `stub` is removed. Local-dev parity is no longer a goal.
- **Single uvicorn worker**: in-process singletons (`event_bus`, `execution_registry`, `runner_registry`) remain process-local; do not introduce horizontal-scaling assumptions
- **Test policy**: bug fixes (ISS-01..06, FE-SEC) ship with a regression test that would have caught them; cleanups (ISS-09, ISS-10, ISS-13, UI-P6) do not require new tests
- **Comment policy** (from ISS-13): default to no comments; comments must explain a non-obvious *why* and stand alone without project context; never name people, plan sections, ACs, review rounds, or describe history
- **Atomicity**: each phase commits its artifacts; mechanical sweeps (ISS-13, UI-P6) land as single behaviour-neutral PRs
- **Working tree**: starting baseline is commit `13fd93f57` on `master`; main branch is `main` (per `git status` snapshot)
- **Scope discipline**: only findings from `docs/reviews/code-review/`, `docs/reviews/ui-design-review/`, `docs/reviews/frontend/` are in scope; nothing else

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Hardening target = internal beta / demo-stable, not production | PoC fragility is the driver; no external launch date; auth + scaling deferred to next milestone | — Pending |
| Strictly stabilisation, no new product features | User explicitly chose "no new features" path; cleaner phase boundaries | — Pending |
| Mirror review-doc structure in phase shape | Reviews are already ordered and grouped; mirroring preserves the merge-order rationale and reduces planning overhead | — Pending |
| Tests for bugs, not for cleanups | Concentrates test-writing effort on the correctness fixes that matter; UI/cleanup gets visual/smoke coverage | — Pending |
| Top-level `docs/reviews/` files (test-failures-mapping, unmapped-findings) deferred to a follow-up milestone | Includes a critical HMAC sign/verify bug on non-ASCII and a `QueueFull`-swallow that drops events silently — both real but out of declared scope. Documented here so the next milestone scoper sees them. | ⚠️ Revisit (next milestone) |
| Remove `kloc_runner_mode=stub` (ISS-12) | Local-dev-without-Docker is no longer a goal; current `stub` mode silently degrades into a broken state | — Pending |
| Replace `<CopilotSidebar>` with inline `<CopilotChat>` (UI-P3) | The single highest-leverage UI fix; addresses the "75% black space" complaint at its source | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-16 after initialization*
