# Roadmap: kloc-agent — Hardening Milestone

**Created:** 2026-05-16
**Granularity:** Standard (6 phases)
**Project mode:** Standard (horizontal layers — no per-phase MVP slice)
**Source of phase structure:** Mirrors `docs/reviews/code-review/issues.md` "Suggested merge order", `docs/reviews/ui-design-review/implementation-plan.md` phases, and the 7 category files in `docs/reviews/frontend/`

---

## Phase Overview

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|------------------|
| 1 | Backend AG-UI & runner correctness | 4/4 | Complete   | 2026-05-16 |
| 2 | Backend settings & boot contract | 3/3 | Complete   | 2026-05-16 |
| 3 | Backend cleanup & comment sweep | 5/5 | Complete   | 2026-05-16 |
| 4 | UI foundations & component reskin | 2/2 | Complete   | 2026-05-16 |
| 5 | UI structural fix, polish, a11y | 4/4 | Complete   | 2026-05-16 |
| 6 | Frontend code quality | 4/4 | Complete   | 2026-05-16 |

**Coverage:** 26 / 26 v1 requirements mapped ✓

---

## Phase Details

### Phase 1: Backend AG-UI & runner correctness
**Goal:** Eliminate the event-ordering and reconnect bugs that violate AG-UI lifecycle invariants and lose audit events. The resume / cursor-replay regression and the audit-completeness gap both come from this cluster.
**Requirements:** ISS-01, ISS-02, ISS-03, ISS-04, ISS-06
**UI hint:** no
**Test policy:** every requirement ships with a regression test that would have caught it
**Success Criteria:**
1. AG-UI lifecycle ordering invariant holds: under orphan-frame → `RUN_STARTED` → terminal scenarios, subscribers always see `RUN_STARTED` at index 0 (unit test asserts)
2. Concurrent reconnect `POST /v1/sessions/{id}/stream` for the same `(session_id, run_id)` does not create a second `_persist_events` task and does not double-append to the execution ring (unit + integration test exercises the double-spawn path)
3. `AuditHookSender` graceful shutdown drains `_after_queue`; post-shutdown audit-row count matches pre-shutdown completed tool-call count (unit test asserts)
4. Concurrent `RUN_FINISHED` of run A and `RUN_STARTED` of run B for the same session does not wipe B's `active_by_session` mapping; B's first intermediate frame is delivered, not buffered as orphan (unit test asserts the CAS guard)
5. Runner `channel.py` reconnect after mid-stream transport reset preserves the in-flight yielded frame; `RUN_FINISHED` is never silently lost on a transport-loss path (unit test simulates the reconnect-during-emit scenario)

**Plans:** 4/4 plans complete
- [x] 01-01-PLAN.md — ISS-01 ordering + ISS-04 CAS guard in src/api/internal.py (regression tests)
- [x] 01-02-PLAN.md — ISS-02 persister dedup keyed by (session_id, run_id) in src/api/stream.py (unit + integration regression tests)
- [x] 01-03-PLAN.md — ISS-03 drain _after_queue in AuditHookSender.stop() (regression test)
- [x] 01-04-PLAN.md — ISS-06 last_inflight prepend on channel reconnect (regression test)

### Phase 2: Backend settings & boot contract
**Goal:** Make misconfiguration surface at boot, never inside the runner. Remove the silent-degradation `stub` runner mode. Ensure HMAC fallback cannot use the placeholder secret in production.
**Requirements:** ISS-05, ISS-07, ISS-12
**UI hint:** no
**Test policy:** ISS-05 and ISS-07 ship with validator tests; ISS-12 ships with an updated test suite that no longer uses `KLOC_RUNNER_MODE=stub`
**Success Criteria:**
1. `llm_provider` and `llm_model_id` always flow through `Settings`; raw `os.environ.get` reads removed from `src/api/stream.py`; missing or mismatched provider key raises at boot, not at first LLM call in the runner
2. `kloc_runner_mode` removed from `Settings`, `.env.example`, and compose configs; lifespan in `src/main.py` unconditionally constructs `DockerRunner`; an `ImportError` or construction failure aborts boot loudly
3. Settings validator raises when `allow_hmac_fallback=True` and `kloc_hook_secret == "dev-secret-please-rotate"` and `stub_mode=False` (validator unit test)
4. All previously-stub-mode unit and integration tests pass by injecting a fake `Runner` via `RunnerRegistry.set_runner()` instead of relying on `KLOC_RUNNER_MODE=stub`

### Phase 3: Backend cleanup & comment sweep
**Goal:** Land the low-risk hardening items plus the 35-file mechanical comment sweep as a single behaviour-neutral commit. Codify the comment policy so the rot does not return.
**Requirements:** ISS-08, ISS-09, ISS-10, ISS-11, ISS-13
**UI hint:** no
**Test policy:** cleanup does not require new tests (per project policy); ISS-13 lands as one behaviour-neutral commit; existing test suite must remain green
**Success Criteria:**
1. `_diag` calls in `src/api/internal.py` and `src/api/webhooks.py` no longer write to stderr by default; gated by `Settings.diag_events` (or `KLOC_DIAG` env); container logs are quiet under normal traffic
2. `app.state.active_run_by_session` and `app.state.pending_pre_run_started` no longer use PEP-526-discarded annotated assignments; either annotations are removed or an `AppState` dataclass replaces ad-hoc attribute access
3. `is_alive` thundering-herd on `get_or_spawn` is addressed (~50 ms result cache on the entry, or documented in-code as accepted with rationale)
4. `ClientDisconnect` response shape in `src/api/internal.py:271-283` distinguishes "no bytes received" from "some frames received then disconnect" (different status code or response field)
5. Comment sweep across the 35 files lands as one mechanical PR removing the ~161 offending comments (devs, plan §, ACs, B-DIAG/B-INFRA tags, historical narrative); comment policy added to `CLAUDE.md` (or `CONTRIBUTING.md`); `grep -rE 'dev-[0-9]|reviewer-[0-9]|plan §|B-DIAG|B-INFRA|AC[0-9]+|Phase [0-9]' src/ runner/ --include='*.py'` returns zero matches

### Phase 4: UI foundations & component reskin
**Goal:** Lock the four open design decisions, install the styling stack, and reskin existing components onto the new token theme — without changing the chat-view structure yet. App must still work at every step.
**Requirements:** UI-P0, UI-P1, UI-P2
**UI hint:** yes
**Test policy:** visual smoke check only (manual); no new automated test coverage required for visual changes
**Success Criteria:**
1. Four design decisions committed in writing: (a) styling stack choice (Tailwind v4 vs CSS Modules), (b) shadcn/ui adoption (yes/no), (c) theme(s) (dark only vs dark + light), (d) font loading method (`next/font` vs `@import`)
2. Styling stack installed; `globals.css` replaced with token theme from `design-direction.md`; chosen display + body + mono fonts loaded; atmosphere background (radial gradient + grain overlay) applied to `body`; CopilotKit CSS variables overridden so chat panel inherits the new theme
3. `ToolCallCard.tsx`, `AgentBody.tsx`, extracted `SessionPicker.tsx`, and chat-view header in `page.tsx` reskinned onto the token theme; no inline `style={{}}` blocks remain in these files; `grep -rn 'style={{' frontend/src/{components,app}` shows only the files not yet reskinned in this phase
4. App renders cleanly at every commit: dev server boots, session picker shows, chat view still works (even though it still has the floating sidebar — that's Phase 5)

### Phase 5: UI structural fix, polish, accessibility, dead code
**Goal:** Fix the "75% black space" complaint by replacing `<CopilotSidebar>` with inline `<CopilotChat>` + a new `SessionRail`. Then layer in polish, accessibility, and dead-code deletion.
**Requirements:** UI-P3, UI-P4, UI-P5, UI-P6
**UI hint:** yes
**Test policy:** visual smoke check + manual a11y audit (no new automated test coverage required)
**Success Criteria:**
1. `<CopilotSidebar>` replaced with inline `<CopilotChat>`; new `SessionRail.tsx` houses session card + runner-state pill + artifacts list; chat view fills the viewport (no large empty background) on a 1440×900 viewport; artifacts wiring moved from `AgentBody` to `SessionRail`
2. Polish layer lands: picker page entrance animation (220 ms title fade-up + staggered 20 ms eyebrow/button/list), hover-shift on session rows (`translateX(3px)` + arrow), runner-state dot glow, streaming indicator (3-dot pulse) in chat input, tool-card chevron rotation on `<details[open]>`, `<title>`/`<meta>` polish in `layout.tsx`
3. Accessibility audit cleared per A-1..A-5: textarea has accessible name (verified or fixed within `<CopilotChat>`), `--text-mute`/`--text-dim` tokens replace `opacity:0.6` text and meet WCAG AA contrast in the chosen theme(s), back-glyph has `aria-label="Back to sessions"`, session row uses `aria-busy` + scoped `cursor: wait`, heading hierarchy is h1 picker / h2 chat session subtitle
4. Dead UI modules deleted: `src/components/ChatWindow.tsx`, `src/components/Composer.tsx`, `src/lib/agui-http-agent.ts`, `src/utils/sseParser.ts`; no imports of these files remain in the tree

### Phase 6: Frontend code quality
**Goal:** Resolve the 36 findings across the 7 `docs/reviews/frontend/` category files. Highest-leverage items first: CopilotRuntime hoist + CopilotKit lazy-load.
**Requirements:** FE-PERF, FE-BUNDLE, FE-ROUTES, FE-DATA, FE-SEC, FE-QUALITY
**UI hint:** yes
**Test policy:** FE-SEC items ship with regression tests where applicable; other categories ship with type-check + lint + manual smoke check
**Success Criteria:**
1. CopilotRuntime allocated once at module scope (not per request) in `src/app/api/copilotkit/route.ts`; CopilotKit + `<CopilotChat>` dynamic-imported on the chat page so the session-picker route does not ship the chat bundle (verified by `next build` route bundle audit)
2. Server/route findings in `docs/reviews/frontend/server-and-routes.md` cleared on `agent-proxy/route.ts` and `copilotkit/route.ts` (SSE lifecycle, request scope, runtime allocation)
3. Data-fetching findings in `docs/reviews/frontend/data-fetching.md` cleared: `listSessions` and `listMessages` have dedup, `AbortController` propagation on unmount/navigation, and a clear error surface for both
4. Security findings in `docs/reviews/frontend/security.md` cleared: `console.warn` body dump behind a debug flag, CSRF posture reviewed and documented, input-trust tightening on the proxied body in `agent-proxy/route.ts` (regression test where applicable)
5. Performance findings (`performance.md`) and code-quality findings (`code-quality.md`) cleared: inline objects memoized/hoisted, hook dependencies stabilised, type widening on `IncomingBody` tightened; `tsc --noEmit` and `eslint` clean across `frontend/src/**`

---

## Dependencies

```
Phase 1 ──┐
Phase 2 ──┤   (Phase 1, 2, 3 are within the backend — sequential by file overlap and merge-order rationale)
Phase 3 ──┘
            │
Phase 4 ────┤  (Phase 4 can technically start in parallel with backend work since they touch disjoint trees; sequential here to keep cognitive load focused)
            │
Phase 5 ────┤  (Phase 5 depends on Phase 4 — needs the token theme and reskinned components before swapping the layout)
            │
Phase 6 ────┘  (Phase 6 depends on Phase 5 — bundle audit and routing changes are easier once the UI is stable)
```

Within each phase, plan-level dependencies will be derived during `/gsd:plan-phase N`. Plans operating on disjoint files run in parallel per `config.json:parallelization=true`.

---

## Notes for Planning

- **Subagents not installed:** `/Users/michal/.claude/agents/` does not yet contain `gsd-planner`, `gsd-phase-researcher`, etc. Run `npx get-shit-done-cc@latest --global` before `/gsd:plan-phase 1`, or planning will operate inline without specialist subagents.
- **Phase 1 → 3 work mostly inside `src/api/internal.py`, `src/api/stream.py`, `src/runner_mgmt/registry.py`, `runner/hooks/audit.py`, and `runner/channel.py`** — see `.planning/codebase/CONCERNS.md` "Fragile Areas" for the concurrency-model docstring that all changes must respect.
- **Phase 4 design decisions (UI-P0) gate everything downstream in UI**. They are a true blocker. Plan Phase 4 first; revisit UI-P0 with the user during `/gsd:discuss-phase 4`.
- **Phase 6 FE-BUNDLE work depends on the final layout from Phase 5**. Do not start bundle-splitting until Phase 5 is merged.

---

*Roadmap created: 2026-05-16*
*Last updated: 2026-05-16 after initial creation*
