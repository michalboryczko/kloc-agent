---
phase: 6
plan: 03
subsystem: frontend
tags: [frontend, data-fetching, abort-controller, dedup, error-surface]
requires: [06-01, 06-02]
provides:
  - AbortSignal threading through every src/lib/api.ts call
  - ApiError + NetworkError classes for differentiated error surfaces
  - AbortController-based dedup for in-flight pick fetches
  - User-facing error messages distinguish backend rejection vs network failure
affects:
  - frontend/src/lib/api.ts
  - frontend/src/app/page.tsx
  - frontend/__tests__/api-errors.test.ts (new)
tech-stack:
  added: []
  patterns:
    - "useRef<AbortController> for in-flight-request dedup"
    - "Error subclass + instanceof for UI error differentiation"
key-files:
  created:
    - frontend/__tests__/api-errors.test.ts
  modified:
    - frontend/src/lib/api.ts
    - frontend/src/app/page.tsx
decisions:
  - "Keep the existing options-object signatures (e.g. listSessions({ includeClosed, signal })) instead of adding signal as a separate positional arg. Less ambiguous at call sites."
  - "Don't use TypeScript parameter-property shorthand (`public readonly status`) — Node 25's --experimental-strip-types is strip-only and rejects it. Explicit field declarations achieve the same shape."
  - "D-2 (full SWR adoption) and D-4 (pagination UI) remain deferred. They were flagged as scope-creep in 06-CONTEXT — current api.ts is simple enough."
metrics:
  completed: 2026-05-16
---

# Phase 6 Plan 03: AbortController + dedup + error differentiation Summary

One-liner: Plumb AbortSignal through api.ts, differentiate ApiError /
NetworkError for clearer UI messages, and add dedup-with-cancel for
in-flight session picks.

## What Changed

### 1. AbortSignal threading through src/lib/api.ts (D-1)

All five helpers accept an optional `signal?: AbortSignal`:
- `createSession({ signal })`
- `listSessions({ includeClosed?, signal? })`
- `getSession(id, { signal })`
- `listMessages(id, { after?, limit?, signal? })`
- `closeSession(id, { signal })`

Signals propagate to the underlying `fetch` via the request init. When
the signal aborts mid-flight, `fetch` throws `AbortError` which `safeFetch`
re-throws unchanged (so callers can ignore it on unmount).

### 2. ApiError + NetworkError classes (D-3)

```ts
export class ApiError extends Error {
  readonly status: number;
  readonly body: string;
  // ...
}

export class NetworkError extends Error {
  readonly cause?: unknown;
  // ...
}
```

- `safeFetch()` wraps `fetch()`; non-Abort errors become `NetworkError`.
- `jsonOrThrow()` converts non-2xx responses into `ApiError(status, body)`.
- `AbortError` propagates unchanged so callers can distinguish "user
  navigated away" from "request actually failed".

### 3. AbortController + dedup in page.tsx (D-1, D-2 lite)

- The `useEffect` that calls `listSessions()` now creates an
  `AbortController`, passes `signal` to `listSessions`, and aborts in
  the cleanup callback.
- A `pickCtrlRef = useRef<AbortController | null>(null)` tracks the
  in-flight user-pick fetch. `pickExisting`, `startNew`, and `onBack`
  all call `abortPick()` first — so rapid retries and back-navigation
  cancel the previous request immediately.
- Each `setBusyId(null)` / `setError(...)` write is guarded by
  "is this still my controller?" so a late-resolving prior fetch
  cannot stomp on a fresh request's UI state.

### 4. Differentiated error display in page.tsx (D-3)

`formatError()` returns:
- `ApiError` → `"backend ${status}: ${body preview}"`
- `NetworkError` → `"network unreachable — is the backend running?"`
- other `Error` → fallback to `.message`

The user now sees actionable text instead of the generic
"Failed to fetch" / "TypeError" surface.

## Tests Added

`frontend/__tests__/api-errors.test.ts` — 5 assertions:

```
✔ ApiError is an Error subclass with status + body fields
✔ ApiError accepts a custom message
✔ NetworkError is an Error subclass and is not an ApiError
✔ NetworkError preserves cause
✔ instanceof discriminates the two classes in a catch
```

Combined with Plan 06-02's 20 validator assertions: 25 tests, all
passing via `npm test`.

## Fetch Lifecycle Audit

| Surface | Before | After |
|---------|--------|-------|
| `listSessions` on mount | cancelled-flag pattern; underlying fetch kept running | AbortController; underlying fetch aborts on unmount/nav |
| `listMessages` on pick | no signal; result discarded if user navigated back | AbortController via pickCtrlRef; previous pick cancelled on retry |
| `createSession` on new | no signal; result discarded if user navigated back | AbortController via pickCtrlRef; previous create cancelled on retry |
| Error surface | `e instanceof Error ? e.message : String(e)` (raw `"Failed to fetch"`) | differentiates ApiError vs NetworkError vs other; user-actionable text |
| `closeSession` | no signal | accepts signal |
| `getSession` | no signal | accepts signal |

## Verification

- `cd frontend && npx tsc --noEmit` — clean.
- `cd frontend && npm test` — 25/25 pass.
- `cd frontend && npm run build` — succeeded.

## Deviations from Plan

**Rule 1 - Bug:** Node 25's `--experimental-strip-types` rejects TypeScript
parameter-property shorthand (`constructor(public readonly status: number)`).
Initial implementation used the shorthand; the `api-errors` test failed
with `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`. Fixed by switching to explicit
field declarations + assignment in the constructor body. Same behavior,
same TS visibility, but compatible with Node's strip-only TS support.
Captured in commit `8bc019ce5`.

## Commits

- `951d90ba4` docs(06-03): plan FE-DATA
- `adc73b9c2` feat(06-03): add ApiError + NetworkError + AbortSignal threading to api.ts (D-1, D-3)
- `ce7f6512d` fix(06-03): AbortController + dedup + error differentiation in page.tsx (D-1, D-3)
- `8bc019ce5` test(06-03): api-errors smoke test + drop TS parameter properties (FE-DATA)

## Self-Check: PASSED

- FOUND: `frontend/__tests__/api-errors.test.ts`
- FOUND: commits `951d90ba4`, `adc73b9c2`, `ce7f6512d`, `8bc019ce5` in `git log`
- npm test reports 25 / 25 passing tests across both test files.
