# Phase 6: Frontend code quality - Context

**Gathered:** 2026-05-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous) — 6 decisions locked in batch

<domain>
## Phase Boundary

Resolve the 36 findings catalogued across `docs/reviews/frontend/*.md`. The
authoritative source for each finding is its category-specific review file.
The highest-leverage items are CopilotRuntime hoist (perf) and
CopilotKit lazy-load (bundle).

In scope: FE-PERF, FE-BUNDLE, FE-ROUTES, FE-DATA, FE-SEC, FE-QUALITY.

Out of scope: visual design changes (Phases 4/5 already shipped the
reskin), new features, accessibility work beyond what's in
`accessibility.md` (Phase 5 cleared most a11y items already).

</domain>

<decisions>
## Implementation Decisions

### Dynamic import
- Use `next/dynamic` with `ssr: false` to lazy-load CopilotKit + `<CopilotChat>`
  ONLY on the chat-view route. Session picker route must not ship the
  CopilotKit bundle. Verify with `next build`'s per-route bundle table.

### CopilotRuntime hoist
- Allocate `const runtime = new CopilotRuntime({ ... })` at module scope in
  `frontend/src/app/api/copilotkit/route.ts`. The route handler reuses it
  across requests instead of re-allocating per request.

### AbortController propagation
- Pass an `AbortSignal` into `listSessions` / `listMessages` /
  `createSession` etc. (all `frontend/src/lib/api.ts` calls). React hooks
  manage the controller lifecycle via `useEffect` cleanup. Per-hook, not
  per-fetch.
- Hook dedup: when the same hook re-fires due to dep change, the previous
  in-flight request is aborted; the new one proceeds.

### Debug flag for body dump
- Replace `console.warn(...)` body-dump call sites with
  `if (process.env.NEXT_PUBLIC_DEBUG_HTTP === "true") { console.warn(...) }`.
- Default off in production; opt-in via `.env.local`.

### Input trust tightening
- `agent-proxy/route.ts`: validate body via the existing
  `RunAgentInput` type from `@ag-ui/client`. Don't introduce zod just for
  this — the AG-UI type contract is sufficient. If the body fails the
  structural check (missing required fields), respond `400`.

### Plan structure
- 4 plans by category grouping:
  - **06-01 (FE-PERF + FE-BUNDLE)** — CopilotRuntime hoist, CopilotKit
    dynamic import, inline-object memoization, hook dep stabilisation.
  - **06-02 (FE-ROUTES + FE-SEC)** — SSE lifecycle on agent-proxy/route.ts
    and copilotkit/route.ts; security review items (body dump debug-gate,
    CSRF posture documentation, input-trust tightening on agent-proxy
    body).
  - **06-03 (FE-DATA)** — dedup + AbortController propagation on
    listSessions / listMessages / createSession; clear error surfacing.
  - **06-04 (FE-QUALITY + cleanup)** — `IncomingBody` type widening fix,
    DRY refactors, trivial cleanups; `tsc --noEmit` + `eslint` clean.

### CSRF posture
- Document the existing posture in a brief inline comment or
  `frontend/docs/csrf.md`. Backend is single-origin same-site behind
  Docker compose; CSRF is not a v1 threat. Document explicitly so future
  multi-origin deployments know what changed.

### Test policy
- FE-SEC: regression test where applicable (e.g., agent-proxy input-trust:
  a unit test asserting that a malformed body returns 400 and does not
  reach the backend).
- All other categories: type-check + lint + smoke. No new automated
  tests required.

### Claude's Discretion
- Exact file-by-file refactor sequence within each plan.
- Whether to extract a `useAbortable<T>` helper hook or inline the pattern.
- Memo helper boundaries (e.g., extract a `selectors.ts` for stable refs).

</decisions>

<code_context>
## Existing Code Insights

### Review files (authoritative source for each requirement)
- `docs/reviews/frontend/performance.md` — FE-PERF findings (memoization,
  hook deps, large objects).
- `docs/reviews/frontend/bundle-and-loading.md` — FE-BUNDLE findings.
- `docs/reviews/frontend/server-and-routes.md` — FE-ROUTES findings on
  `agent-proxy/route.ts` + `copilotkit/route.ts`.
- `docs/reviews/frontend/data-fetching.md` — FE-DATA findings.
- `docs/reviews/frontend/security.md` — FE-SEC findings.
- `docs/reviews/frontend/code-quality.md` — FE-QUALITY findings.
- `docs/reviews/frontend/accessibility.md` — already addressed in Phase 5,
  spot-check only.

### Reusable Assets
- Phase 5 left `frontend/src/lib/utils.ts` (shadcn `cn` helper) and the
  Tailwind v4 + tokens system. Reuse for any new helpers.
- `frontend/src/lib/api.ts` is the central HTTP wrapper — modify in place.

### Integration Points
- `frontend/src/app/api/copilotkit/route.ts` — runtime hoist.
- `frontend/src/app/api/agent-proxy/route.ts` — SSE lifecycle, input-trust,
  debug-gate.
- `frontend/src/lib/api.ts` — AbortSignal propagation.
- `frontend/src/app/page.tsx` — dynamic import for CopilotKit.
- `frontend/src/components/SessionRail.tsx`, `SessionPicker.tsx`,
  `AgentBody.tsx`, `ToolCallCard.tsx`, `A11yChatTextarea.tsx`,
  `StreamingDots.tsx` — inline-object memoization, hook dep audits.

</code_context>

<specifics>
## Specific Ideas

- The CopilotKit lazy-load decision implicates `page.tsx`'s import
  structure. The chat-view branch (when `pathname` includes a session)
  should be the only thing dynamic-loading CopilotKit. Session picker
  must be CopilotKit-free.
- Bundle audit: after the refactor, `next build` output should show the
  session-picker route's bundle is materially smaller than before.
  Capture the before/after delta in 06-01-SUMMARY.
- FE-QUALITY overlaps with UI-P6 (Phase 5 deleted ChatWindow + Composer
  + agui-http-agent + sseParser). Ensure no Phase 6 plan re-references
  those files.

</specifics>

<deferred>
## Deferred Ideas

- Replacing the entire fetch layer with TanStack Query or SWR — v2 concern;
  current `frontend/src/lib/api.ts` is simple enough.
- Adding a runtime-config object for the debug flag (vs env var) — over-
  engineering for v1.
- Service worker / offline support — deferred indefinitely.
- Migration to React Server Components for `page.tsx` — significant
  rewrite, not a quality fix.

</deferred>
