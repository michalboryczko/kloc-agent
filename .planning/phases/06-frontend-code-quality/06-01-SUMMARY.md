---
phase: 6
plan: 01
subsystem: frontend
tags: [frontend, performance, bundle, copilotkit, dynamic-import]
requires: []
provides:
  - CopilotRuntime hoisted to module scope (one allocation, not per-request)
  - CopilotKit + CopilotChat dynamic-imported on chat view via ChatShell
  - INITIAL_AGENT_STATE, AGENT_NAME, RenderToolCall stabilized at module scope
  - SessionPicker row formatting memoized
affects:
  - frontend/src/app/api/copilotkit/route.ts
  - frontend/src/app/page.tsx
  - frontend/src/app/layout.tsx
  - frontend/src/components/ChatShell.tsx (new)
  - frontend/src/components/AgentBody.tsx
  - frontend/src/components/SessionRail.tsx
  - frontend/src/components/SessionPicker.tsx
  - frontend/src/lib/config.ts (new)
tech-stack:
  added: []
  patterns:
    - "next/dynamic ssr:false for client-only heavy modules"
    - "module-scope render components for stable useCopilotAction render prop"
key-files:
  created:
    - frontend/src/components/ChatShell.tsx
    - frontend/src/lib/config.ts
  modified:
    - frontend/src/app/api/copilotkit/route.ts
    - frontend/src/app/page.tsx
    - frontend/src/app/layout.tsx
    - frontend/src/components/AgentBody.tsx
    - frontend/src/components/SessionRail.tsx
    - frontend/src/components/SessionPicker.tsx
decisions:
  - "Center AGENT_NAME in @/lib/config (client). Server routes keep local consts to avoid client-config coupling on server bundles."
  - "Memoize per-row toLocaleString once per `sessions` change rather than per render."
metrics:
  completed: 2026-05-16
---

# Phase 6 Plan 01: CopilotRuntime hoist + CopilotKit dynamic import Summary

One-liner: Hoist CopilotRuntime once at module scope, dynamic-import the
CopilotKit chat shell so the session-picker route no longer ships the
1.5 MB chat bundle, and stabilize a handful of inline render references.

## What Changed

### 1. CopilotRuntime hoisted to module scope (S-1, FE-BUNDLE)

`frontend/src/app/api/copilotkit/route.ts` previously allocated
`CopilotRuntime`, `HttpAgent`, and `handleRequest` on every POST. The only
per-request value was `proxyUrl(req)`, which is always
`${origin}/api/agent-proxy`. Since the proxy is always same-origin, the
URL collapses to a relative `"/api/agent-proxy"` and everything else can
live at module scope.

The POST handler is now a one-line forwarder:
`export const POST = (req: NextRequest) => handleRequest(req);`

### 2. Dynamic-imported CopilotKit on the chat view (B-1, FE-BUNDLE)

Extracted the chat-view markup into `frontend/src/components/ChatShell.tsx`
and loaded it via `next/dynamic` with `ssr: false`. Moved
`@copilotkit/react-ui/styles.css` from `layout.tsx` into `ChatShell.tsx`
so the stylesheet is co-located with the lazy chunk.

### 3. Hoisted module-level constants and stable callbacks (P-2, P-6, CQ-2)

- Created `frontend/src/lib/config.ts` exporting `AGENT_NAME`,
  `INITIAL_AGENT_STATE`, and the shared `Artifact` type. Replaces the
  duplicated `AGENT_NAME = process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent"`
  pattern in two client files and centralizes precedence between the
  `NEXT_PUBLIC_*` (client-baked) and the unprefixed (server) variant.
- Defined `RenderToolCall` as a module-scope component in `AgentBody.tsx`;
  `useCopilotAction({ name: "*", render: RenderToolCall })` now has a stable
  function reference across renders.
- `SessionRail.tsx` calls `useCoAgent({ name, initialState: INITIAL_AGENT_STATE })`
  with the hoisted singleton.

### 4. Memoized SessionPicker row formatting (P-4)

`new Date(s.updated_at).toLocaleString()` per row was running on every
render of `SessionPicker` (re-fired by `busyId` and `error` state
changes). Wrapped in a `useMemo` keyed by `sessions`, so the locale
formatter runs once per data change.

### 5. Clear error state on transitions (D-3 spirit, also FE-DATA D-3)

`pickExisting`, `startNew`, and the new `onBack` handler now all call
`setError(null)` before proceeding. This prevents a stale "failed to load
sessions" message from lingering after a successful retry. (FE-DATA's
fuller error-surface differentiation lands in Plan 06-03.)

## Bundle Audit

Measured by summing the byte total of unique chunks referenced from
`.next/server/app/page_client-reference-manifest.js` after a clean
`next build`. The page client-reference-manifest is what the `/` route
loads when first rendered.

| Variant | Chunks referenced | Total bytes | Notes |
|---------|-------------------|-------------|-------|
| Baseline (pre-plan) | 6 | 2,636,690 | Includes 1.5 MB CopilotKit chunk + 514 KB + 324 KB + 264 KB |
| After plan 06-01 | 3 | 66,299 | All small UI/picker chunks; no CopilotKit |

Net delta: **−2,570,391 bytes (≈2.51 MB / ≈97.5% reduction)** on the
session-picker route's eager client bundle. The CopilotKit chunks still
exist in the build output (`.next/static/chunks/ae4d760366e86099.js`,
1,496,356 bytes; `.next/static/chunks/77bb810f8ae1f556.js`, 626,759
bytes; etc.) — they're now loaded only when `ChatShell` mounts after the
user picks a session.

## Verification

- `cd frontend && npx tsc --noEmit` — clean.
- `cd frontend && npm run build` — succeeded, all 5 static pages generated.
- Bundle audit captured before & after (see above).

## Deviations from Plan

None — plan executed as written. Task 5 (error-state clearing) was folded
into task 4 since the same `page.tsx` rewrite touched the relevant lines;
the work is in commit `e51689666` and not a separate commit.

## Commits

- `83216704a` docs(06-01): plan FE-PERF + FE-BUNDLE
- `1707aa15c` perf(06-01): hoist CopilotRuntime to module scope (S-1, FE-BUNDLE)
- `af280ad95` perf(06-01): hoist RenderToolCall, INITIAL_AGENT_STATE, AGENT_NAME (P-2, P-6, CQ-2)
- `ecf6f9cf9` perf(06-01): memoize SessionPicker row formatting (P-4)
- `e51689666` perf(06-01): dynamic-import CopilotKit on chat view (B-1, FE-BUNDLE)

## Self-Check: PASSED

- FOUND: `frontend/src/lib/config.ts`
- FOUND: `frontend/src/components/ChatShell.tsx`
- FOUND: commits `83216704a`, `1707aa15c`, `af280ad95`, `ecf6f9cf9`, `e51689666` in `git log`
