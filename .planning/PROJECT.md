# kloc-agent

## What This Is

`kloc-agent` is a single-operator AI agent orchestration system: a FastAPI backend spawns disposable Docker runner containers per session, streams Strands-Agents events back over AG-UI to a Next.js/CopilotKit browser UI, and persists every message + audit event to Postgres. Runners connect to an external `kloc-intelligence` MCP server for the actual codebase-knowledge tools. After the v1.0 hardening milestone the PoC is demo-stable for internal beta use.

## Current State (v1.0 — Hardening, shipped 2026-05-16)

- 6 phases / 22 plans / 149 commits since the init-state baseline
- 26/26 v1 requirements verified at code level
- Backend: AG-UI lifecycle/ordering fixed (ISS-01..04, ISS-06), persister dedup race fixed (CR-01 stream_get / CR-02 channel 4xx coverage gaps closed), audit drain on shutdown, all settings route through `Settings`, stub runner mode removed, comment-policy sweep applied
- Frontend: Tailwind v4 + shadcn/ui + Editorial Terminal token theme, `<CopilotSidebar>` replaced with inline `<CopilotChat>` + `SessionRail`, a11y audit cleared, dead-code deleted, CopilotKit dynamic-imported (picker-route bundle 2.64 MB → 66 KB, −97.5%), AbortController + input-trust + debug-gated logging
- Tests: 166+ Python unit/integration passing (was 103 at baseline); 25 frontend tests passing
- Pre-existing baseline e2e failures in `tests/e2e/test_hook_deny.py` and `tests/e2e/test_artifact_lifecycle.py` remain — they pre-date v1.0 and are deferred to a future infra phase
- 22-item operator validation checklist (boot scenarios, visual smoke, a11y manual audit) recorded in `milestones/v1.0-MILESTONE-AUDIT.md`

## Core Value

A single analyst can have a live, resumable, audit-complete agent conversation against an indexed codebase — and trust that every event, message, and tool call is reliably persisted, ordered correctly, and never silently dropped.

This core value held through v1.0 unchanged.

## Requirements

### Validated (shipped v1.0)

**Backend correctness**
- ✓ ISS-01 — `RUN_STARTED` publishes before pre-run orphan flush — v1.0
- ✓ ISS-02 — Persister tasks deduplicated by `(session_id, run_id)` — v1.0
- ✓ ISS-03 — `AuditHookSender.stop()` drains `_after_queue` before cancel — v1.0
- ✓ ISS-04 — CAS-guarded pop on terminal frames — v1.0
- ✓ ISS-05 — `llm_provider` and `llm_model_id` route through `Settings` — v1.0
- ✓ ISS-06 — Runner channel replays last-inflight frame on reconnect — v1.0

**Backend hardening + cleanup**
- ✓ ISS-07 — HMAC fallback validator on default-secret + allow-fallback combo — v1.0
- ✓ ISS-08 — `_diag` writes gated behind `Settings.diag_events` — v1.0
- ✓ ISS-09 — `app.state.*` annotated assignments removed — v1.0
- ✓ ISS-10 — `is_alive` 50ms TTL cache on `RegistryEntry` — v1.0
- ✓ ISS-11 — `ClientDisconnect` distinguishes 499/204 — v1.0
- ✓ ISS-12 — `kloc_runner_mode` removed; Docker is the only mode — v1.0
- ✓ ISS-13 — Comment sweep + policy codified in `CLAUDE.md` — v1.0

**UI design overhaul**
- ✓ UI-P0..UI-P2 — Tailwind v4 + shadcn/ui + Editorial Terminal theme + component reskin — v1.0
- ✓ UI-P3 — `<CopilotSidebar>` → inline `<CopilotChat>` + `SessionRail` — v1.0
- ✓ UI-P4 — Polish animations (entrance cascade, hover-shift, pill glow, chevron rotation) — v1.0
- ✓ UI-P5 — Accessibility audit cleared (A-1..A-5) — v1.0
- ✓ UI-P6 — Dead-code deletion — v1.0

**Frontend code quality**
- ✓ FE-PERF + FE-BUNDLE — CopilotRuntime module-scope; CopilotKit dynamic-imported (97.5% bundle reduction on picker route) — v1.0
- ✓ FE-ROUTES — SSE lifecycle + request-scope hardening — v1.0
- ✓ FE-DATA — AbortController propagation + dedup + ApiError/NetworkError differentiation — v1.0
- ✓ FE-SEC — Body dump behind `NEXT_PUBLIC_DEBUG_HTTP`; CSRF posture documented; agent-proxy input validation — v1.0
- ✓ FE-QUALITY — `IncomingBody` type tightened; DRY refactors; `tsc --noEmit` + `eslint` clean — v1.0

### Active (next milestone)

No active milestone scope. Run `/gsd:new-milestone` when ready to plan the next cycle. Candidate themes already on the radar:

- **Auth / authorization** on `/v1/*` and `/internal/*` (replace `HARDCODED_ANALYST_ID`)
- **Top-level `docs/reviews/` deferred items** — HMAC sign/verify divergence on non-ASCII bodies; `EventBus.publish` `QueueFull`-swallow; `test-failures-root-cause-mapping.md` and `unmapped-findings.md`
- **Pre-existing e2e failures** in `tests/e2e/test_hook_deny.py` and `test_artifact_lifecycle.py`
- **Operator validation backlog** — 22 items from `milestones/v1.0-MILESTONE-AUDIT.md` (4 backend boot, 2 live-stack, 8 UI visual, 6 UI a11y, 4 chat smoke)
- **`ExecutionRegistry.gc()` lifespan wiring** (flagged in CONCERNS.md memory-leak section)
- **`MessageRepo._next_seq` PostgreSQL-sequence rewrite** (single-operator demo load acceptable for v1.0; revisit at scale)

### Out of Scope (unchanged from v1.0)

- **Real-time chat, video posts, mobile, OAuth** — n/a to product class
- **Horizontal scaling / multi-worker uvicorn** — single-worker is intentional; in-process state stays as-is
- **Vendoring `strands_agentskills` git-SHA pin** — accepted risk
- **New product features** — milestone-by-milestone discipline; new features get their own milestone

## Context

- v1.0 baseline was commit `13fd93f57` (renamed `eccd88c65` after the init-state snapshot commit on 2026-05-16)
- Three review-doc directories (`docs/reviews/code-review/`, `docs/reviews/ui-design-review/`, `docs/reviews/frontend/`) drove the entire milestone scope
- v1.0 added the `Editorial Terminal` design system (dark, hairline borders, serif italic display + JetBrains Mono badges, warm amber `#f5a524` accent)
- Frontend tech stack now: Next.js 16.0.8, React 19.2.1, CopilotKit 1.56.5, Tailwind v4, shadcn/ui (Button/Input/Textarea/Card baseline), TypeScript strict
- Backend tech stack unchanged: Python 3.12, FastAPI 0.115, Strands 1.39.0, AG-UI 0.1.18, uv 0.5.4 (locked, no version upgrades in v1.0)

## Constraints

- **Tech stack (backend)**: Python 3.12, FastAPI ≥ 0.115, SQLAlchemy 2.x async + asyncpg, `strands-agents==1.39.0`, `ag-ui-protocol==0.1.18`, `ag_ui_strands==0.1.8`, uv 0.5.4 — locked
- **Tech stack (frontend)**: Next.js 16.0.8, React 19.2.1, CopilotKit 1.56.5, `@ag-ui/client 0.0.42`, TypeScript 5.6 strict, Tailwind v4 + shadcn/ui — locked
- **Runtime mode**: Docker is the only runner mode (ISS-12)
- **Single uvicorn worker**: in-process singletons (`event_bus`, `execution_registry`, `runner_registry`) remain process-local
- **Comment policy** (codified in `CLAUDE.md`): default to no comments; comments must explain a non-obvious *why* and stand alone without project context

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Hardening target = internal beta / demo-stable, not production | PoC fragility was the driver; no external launch date; auth + scaling deferred | ✓ Good (v1.0) |
| Strictly stabilisation, no new product features | Cleaner phase boundaries; user explicitly chose this path | ✓ Good (v1.0) |
| Mirror review-doc structure in phase shape | Reviews were already grouped and ordered; preserved merge-order rationale | ✓ Good (v1.0) |
| Tests for bugs (ISS-01..06, FE-SEC), not for cleanups | Concentrated test-writing on correctness fixes; UI/cleanup got visual/smoke coverage | ✓ Good (v1.0) |
| Remove `kloc_runner_mode=stub` (ISS-12) | Local-dev-without-Docker was no longer a goal; stub mode silently degraded | ✓ Good (v1.0) |
| Replace `<CopilotSidebar>` with inline `<CopilotChat>` (UI-P3) | Highest-leverage UI fix; addressed "75% black space" complaint at the source | ✓ Good (v1.0) |
| Tailwind v4 + `@theme` tokens + shadcn/ui (UI-P0) | Codebase had 4 components — cheapest moment to adopt; Tailwind v4 `@theme` is a token system in CSS, maps 1:1 to design-direction.md | ✓ Good (v1.0) |
| CSS-only animations over Framer Motion (UI-P4) | All animations under 300ms; Framer was over-engineered for the surface | ✓ Good (v1.0) |
| CopilotKit dynamic-import on chat route only (FE-BUNDLE) | Picker-route bundle dropped 97.5% (2.64 MB → 66 KB) | ✓ Good (v1.0) |
| AG-UI `RunAgentInput` type for input trust on agent-proxy (FE-SEC) | Avoided introducing zod just for one validation path; type contract was sufficient | ✓ Good (v1.0) |
| Top-level `docs/reviews/` files deferred to follow-up milestone | Includes HMAC sign/verify non-ASCII bug + `QueueFull`-swallow — real but explicitly out of v1.0 scope | ⚠️ Revisit (next milestone) |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state
5. Move shipped requirements to Validated; clear Active for next milestone

---
*Last updated: 2026-05-16 after v1.0 milestone shipped*
