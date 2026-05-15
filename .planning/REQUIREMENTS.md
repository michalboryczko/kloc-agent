# Requirements: kloc-agent — Hardening Milestone

**Defined:** 2026-05-16
**Core Value:** A single analyst can have a live, resumable, audit-complete agent conversation against an indexed codebase — and trust that every event, message, and tool call is reliably persisted, ordered correctly, and never silently dropped.

**Source documents (in-scope):**
- `docs/reviews/code-review/issues.md` (backend: ISS-01..13)
- `docs/reviews/ui-design-review/` (UI: implementation-plan Phases 0–6)
- `docs/reviews/frontend/` (frontend quality: 7 category files)

**Test policy:** Bug fixes (ISS-01..06 + FE-SEC items) ship with a regression test that would have caught them. Cleanups (ISS-09, ISS-10, ISS-13, UI-P6, FE-QUALITY) do not require new tests; UI gets visual/smoke check only.

---

## v1 Requirements

### Backend — Correctness Bugs

<!-- From docs/reviews/code-review/issues.md §Open issues, Critical/High tier. Each ships with a regression test. -->

- [ ] **ISS-01**: `RUN_STARTED` publishes before the pre-`RUN_STARTED` orphan buffer is flushed; subscribers see `RUN_STARTED` at index 0 (Critical)
- [ ] **ISS-02**: Persister tasks are keyed by `(session_id, run_id)`; concurrent reconnect POSTs do not double-subscribe the event bus or double-append to the execution ring (High)
- [ ] **ISS-03**: `AuditHookSender.stop()` drains `_after_queue` before cancelling the worker; no `tool_call.completed` rows lost on warm-idle eviction or graceful shutdown (High)
- [ ] **ISS-04**: `RUN_FINISHED`/`RUN_ERROR` pop on `active_by_session` is a compare-and-swap against `run_id`; concurrent handover does not wipe a fresh run's mapping (High)
- [ ] **ISS-05**: `llm_provider` and `llm_model_id` always route through `Settings`; `os.environ.get` reads in `src/api/stream.py:347-353` are removed; missing provider key fails at boot, not in the runner (Medium)
- [ ] **ISS-06**: Runner `channel.py` reconnect prepends the last yielded frame (or equivalent watermark) so transport-loss `RUN_FINISHED` symptoms cannot occur (Medium)

### Backend — Hardening & Cleanup

<!-- From docs/reviews/code-review/issues.md §Open issues, Medium/Low tier. Cleanups do not require new tests. -->

- [ ] **ISS-07**: Settings validator raises when `allow_hmac_fallback=True` and `kloc_hook_secret == "dev-secret-please-rotate"` and not `stub_mode` (Medium)
- [ ] **ISS-08**: `_diag` writes in `src/api/internal.py` and `src/api/webhooks.py` are gated behind `Settings.diag_events` (or `KLOC_DIAG` env); default off (Low)
- [ ] **ISS-09**: `app.state.*` annotated assignments in `src/main.py:83,88` are removed (or migrated to an `AppState` dataclass) (Low)
- [ ] **ISS-10**: `is_alive` thundering-herd cost addressed — either ~50 ms result cache on the entry, or documented as accepted with a comment (Low)
- [ ] **ISS-11**: `ClientDisconnect` response in `src/api/internal.py:271-283` distinguishes "no bytes" from "some frames then disconnect" (status code or shape) (Low)
- [ ] **ISS-12**: `kloc_runner_mode` removed from `Settings` and `.env.example`; lifespan unconditionally constructs `DockerRunner`; `ImportError`/construction failure fails boot loudly; tests previously using `KLOC_RUNNER_MODE=stub` inject a fake `Runner` via `RunnerRegistry.set_runner()` instead (Medium)
- [ ] **ISS-13**: Mechanical comment sweep across the 35 identified files (~161 offending comments naming devs, plan §, ACs, B-DIAG/B-INFRA tags, or narrating history); lands as a single behaviour-neutral PR; comment policy added to `CLAUDE.md` (or `CONTRIBUTING.md`) (Medium)

### UI Design — Visual Overhaul

<!-- From docs/reviews/ui-design-review/implementation-plan.md Phases 0–6. UI gets visual/smoke check, not full test coverage. -->

- [ ] **UI-P0**: Four design decisions are written and committed: (a) styling stack (Tailwind v4 vs CSS Modules), (b) shadcn/ui adoption (yes/no), (c) dark only vs dark + light, (d) font loading via `next/font` vs `@import`
- [ ] **UI-P1**: Styling foundations installed and locked: chosen styling stack configured, `globals.css` replaced with token theme (from `design-direction.md`), display + body + mono fonts loaded, atmosphere background (radial gradient + grain) on `body`, CopilotKit CSS variable overrides applied. App still renders.
- [ ] **UI-P2**: Component reskin complete: `ToolCallCard.tsx`, `AgentBody.tsx`, extracted `SessionPicker.tsx` from `page.tsx`, and chat-view header + two-column grid in `page.tsx` all use the token theme; no inline `style={{}}` blocks remain in these files
- [ ] **UI-P3**: `<CopilotSidebar>` replaced with inline `<CopilotChat>` plus a new `SessionRail.tsx` (session card, runner-state pill, artifacts list); the "75% black space" complaint (F-1) is visibly resolved on a 1440×900 viewport
- [ ] **UI-P4**: Polish layer landed: picker page entrance animation (220 ms title + staggered 20 ms), hover-shift on session rows (`translateX(3px)` + arrow), runner-state dot glow, streaming indicator (3-dot pulse) in chat input, tool-card chevron rotation, `<title>` + `<meta>` polish in `layout.tsx`
- [ ] **UI-P5**: Accessibility audit cleared: A-1 textarea name (verified or fixed in `<CopilotChat>`), A-2 `--text-mute`/`--text-dim` tokens with measured WCAG AA contrast in chosen theme(s), A-3 `aria-label="Back to sessions"` on back glyph, A-4 row-scoped `aria-busy` + `cursor: wait`, A-5 heading hierarchy (h1 picker, h2 chat session subtitle)
- [ ] **UI-P6**: Dead UI modules deleted: `src/components/ChatWindow.tsx`, `src/components/Composer.tsx`, `src/lib/agui-http-agent.ts`, `src/utils/sseParser.ts`

### Frontend — Code Quality

<!-- From docs/reviews/frontend/*.md — 36 findings across 7 categories. Each requirement covers one category file. -->

- [ ] **FE-PERF**: All 7 findings in `docs/reviews/frontend/performance.md` resolved (inline objects hoisted/memoized, hook deps stabilised, re-render hot spots addressed)
- [ ] **FE-BUNDLE**: All 4 findings in `docs/reviews/frontend/bundle-and-loading.md` resolved — most importantly, `CopilotRuntime` hoisted out of the per-request POST handler in `src/app/api/copilotkit/route.ts`, and CopilotKit dynamic-imported on the chat page so the session picker does not ship the chat bundle
- [ ] **FE-ROUTES**: All 5 findings in `docs/reviews/frontend/server-and-routes.md` resolved (SSE lifecycle, request scope, runtime allocation on `agent-proxy` and `copilotkit` routes)
- [ ] **FE-DATA**: All 4 findings in `docs/reviews/frontend/data-fetching.md` resolved (lifecycle, dedup, abort, error surface for `listSessions` and `listMessages`)
- [ ] **FE-SEC**: All 4 findings in `docs/reviews/frontend/security.md` resolved — including moving the `console.warn` body dump behind a debug flag, CSRF posture review on `agent-proxy`, input-trust tightening on the proxied body (ships with regression test where applicable)
- [ ] **FE-QUALITY**: All 7 findings in `docs/reviews/frontend/code-quality.md` resolved (DRY, type widening on `IncomingBody`, trivial cleanups; overlaps with UI-P6 dead-code deletions — coordinate so deletions are not done twice)

---

## v2 Requirements

Items observed during review but deferred to the next milestone. Out-of-scope for the current milestone per `PROJECT.md`.

### Top-Level Review Findings (not in the 3 directories)

- **HMAC-DIVERGENCE**: `runner/hooks/audit.py:36` `_sign()` re-encodes the body with `f"{ts_ms}.{body.decode('utf-8')}".encode("utf-8")` while the verifier at `src/hooks_audit/verify_hmac.py:32` concatenates raw bytes. Diverges on non-ASCII UTF-8 JSON (Unicode tool names or result previews), silently blocking `BeforeToolCall`. *(Confirmed critical; deferred.)*
- **EVENTBUS-QUEUEFULL**: `src/streaming/event_bus.py:24` `publish()` calls `put_nowait` and swallows `QueueFull` — a slow SSE subscriber causes events to vanish with no log line. *(Confirmed critical; deferred.)*
- **GET-SETTINGS-THREADSAFE**: `src/settings.py:93-100` `get_settings()` singleton has no lock; harmless in single-worker but a multi-worker hazard. *(Confirmed high; deferred.)*
- **RUNNER-EXITSTACK**: `runner/__main__.py:81` uses `contextlib.ExitStack` for async MCP clients. *(Confirmed high; deferred.)*
- **TEST-FAILURE-CLUSTER**: Six reported test failures in `docs/reviews/test-failures-root-cause-mapping.md` — most root causes overlap ISS-01..06 and will be fixed indirectly; revisit and confirm green after this milestone.

### Authentication & Multi-Tenant Surface

- **AUTH-01..N**: All `/v1/*` and `/internal/*` endpoint auth, replacement of `HARDCODED_ANALYST_ID`, rate limiting on stream endpoint, per-runner token validation for `/internal` (per `docs/reviews/code-review/2026-05-16-kloc-agent-cr.md` security recs and `CONCERNS.md`)

### Scaling & Operations

- **SCALE-MULTIWORKER**: Move `event_bus`, `execution_registry`, `runner_registry`, `active_run_by_session` to shared broker (Redis pub/sub) for horizontal scaling
- **SCALE-DOCKER**: Decouple runner lifecycle from in-process Docker socket
- **OPS-EXECREG-GC**: Schedule periodic `execution_registry.gc()` from lifespan
- **OPS-MSGSEQ**: Replace `_next_seq` SELECT-max+retry with Postgres sequence per session
- **OPS-HYDRATION**: Delete hydration files immediately after runner reads them, or move to Docker secrets
- **DEPS-AGENTSKILLS**: Vendor or replace `strands_agentskills` git-SHA pin
- **DEPS-AGUI-PIN**: Tighten `ag-ui-protocol` / `ag_ui_strands` version range and enable strict type checking on adapter import sites
- **DEPS-OTEL-PIN**: Pin OpenTelemetry version range in `pyproject.toml`

## Out of Scope

| Feature | Reason |
|---------|--------|
| Authentication / authorization on any endpoint | Demo-stable target is single-operator on compose stack with network isolation; auth defers to next milestone |
| Replacing `HARDCODED_ANALYST_ID = "analyst-poc"` | Same as auth — single-operator demo |
| Rate limiting on stream endpoint | Defers with auth |
| Horizontal scaling / multi-worker uvicorn | Single-worker intentional for this milestone |
| Vendoring or replacing `strands_agentskills` git-SHA pin | Accepted risk; tracked in v2 |
| HMAC sign/verify divergence on non-ASCII bodies | Outside declared scope (top-level `unmapped-findings.md`); tracked in v2 |
| `EventBus.publish` `QueueFull` swallow | Outside declared scope (top-level `unmapped-findings.md`); tracked in v2 |
| New product features (real-time chat, video posts, mobile, OAuth) | Strictly stabilisation milestone; no new product features |
| `ExecutionRegistry.gc()` lifespan wiring | Latent memory leak; deferred unless it surfaces in beta usage |
| `MessageRepo._next_seq` PostgreSQL-sequence rewrite | Accepted for single-operator demo load; deferred to scale work |
| Version upgrades of any pinned dependency | Stack is locked for this milestone |

## Traceability

Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ISS-01 | TBD | Pending |
| ISS-02 | TBD | Pending |
| ISS-03 | TBD | Pending |
| ISS-04 | TBD | Pending |
| ISS-05 | TBD | Pending |
| ISS-06 | TBD | Pending |
| ISS-07 | TBD | Pending |
| ISS-08 | TBD | Pending |
| ISS-09 | TBD | Pending |
| ISS-10 | TBD | Pending |
| ISS-11 | TBD | Pending |
| ISS-12 | TBD | Pending |
| ISS-13 | TBD | Pending |
| UI-P0 | TBD | Pending |
| UI-P1 | TBD | Pending |
| UI-P2 | TBD | Pending |
| UI-P3 | TBD | Pending |
| UI-P4 | TBD | Pending |
| UI-P5 | TBD | Pending |
| UI-P6 | TBD | Pending |
| FE-PERF | TBD | Pending |
| FE-BUNDLE | TBD | Pending |
| FE-ROUTES | TBD | Pending |
| FE-DATA | TBD | Pending |
| FE-SEC | TBD | Pending |
| FE-QUALITY | TBD | Pending |

**Coverage:**
- v1 requirements: 26 total
- Mapped to phases: 0 (populated by roadmap)
- Unmapped: 26 ⚠️ (will be 0 after roadmap)

---
*Requirements defined: 2026-05-16*
*Last updated: 2026-05-16 after initial definition*
