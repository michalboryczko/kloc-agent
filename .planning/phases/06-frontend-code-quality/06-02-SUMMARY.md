---
phase: 6
plan: 02
subsystem: frontend
tags: [frontend, security, routes, sse, csrf, regression-test]
requires: [06-01]
provides:
  - agent-proxy input-trust validation gate
  - debug-gated body-dump logging
  - runtime/dynamic/fetchCache exports on both SSE-producing routes
  - 30s upstream timeout + sanitized error surface
  - documented v1 CSRF posture
affects:
  - frontend/src/app/api/agent-proxy/route.ts
  - frontend/src/app/api/agent-proxy/validation.ts (new)
  - frontend/src/app/api/copilotkit/route.ts
  - frontend/__tests__/agent-proxy-validation.test.ts (new)
  - frontend/docs/csrf.md (new)
  - frontend/package.json
  - frontend/tsconfig.json
tech-stack:
  added: []
  patterns:
    - "Node 25 built-in test runner with --experimental-strip-types (no test framework devDep)"
    - "AbortSignal.any to combine client-disconnect with timeout"
    - "pure-module validator at route trust boundary"
key-files:
  created:
    - frontend/src/app/api/agent-proxy/validation.ts
    - frontend/__tests__/agent-proxy-validation.test.ts
    - frontend/docs/csrf.md
  modified:
    - frontend/src/app/api/agent-proxy/route.ts
    - frontend/src/app/api/copilotkit/route.ts
    - frontend/package.json
    - frontend/tsconfig.json
decisions:
  - "Use Node 25's built-in node:test + --experimental-strip-types instead of installing Vitest. Zero new devDeps; matches the 'minimal scope creep' constraint."
  - "Renamed the local CopilotRuntime instance from `runtime` to `copilotRuntime` so it doesn't collide with the `export const runtime = 'nodejs'` segment-config export."
  - "Body validation is a separate pure module so the regression test can import it without spinning Next.js."
metrics:
  completed: 2026-05-16
---

# Phase 6 Plan 02: SSE lifecycle + input-trust + CSRF posture Summary

One-liner: Add a structural-validation gate, debug-gated logging, SSE
runtime hints, upstream timeout, sanitized error surface, and document
the v1 CSRF posture — all on both Next.js route handlers.

## What Changed

### 1. Pure-module input-trust validator (SEC-2, S-1 fan-out)

New `frontend/src/app/api/agent-proxy/validation.ts`:

- `validateIncomingBody(value)` — structural check on the AG-UI proxy
  body. Returns `{ ok: true, body }` or `{ ok: false, reason }`. Rejects
  non-objects, non-record `state`/`forwardedProps`/`properties`,
  non-array `messages`/`tools`/`context`, non-string `threadId`/`runId`/
  `lastEventId`, messages without `role`, messages with non-string
  `role` or `id`.
- `resolveSessionId(body)` — pick session_id from
  properties → forwardedProps → state (camelCase + snake_case).
- `ensureMessageIds(messages)` — synthesise UUIDs for any caller-supplied
  messages without an `id`.

### 2. agent-proxy route hardening (S-2, S-3, S-4, SEC-2, SEC-3)

`frontend/src/app/api/agent-proxy/route.ts`:

- Declares `runtime = "nodejs"`, `dynamic = "force-dynamic"`,
  `fetchCache = "force-no-store"` so Next 16 cannot statically cache or
  downgrade this SSE-producing route to Edge.
- Body parsed → `validateIncomingBody` gate → 400 on failure before any
  upstream call.
- The diagnostic `console.warn` is gated behind
  `NEXT_PUBLIC_DEBUG_HTTP === "true"` and only logs `Object.keys(...)`,
  never values.
- Upstream fetch uses
  `AbortSignal.any([req.signal, AbortSignal.timeout(30_000)])` —
  combines client-disconnect with a 30s ceiling.
- Failure paths:
  - upstream timeout → log structured + return 504.
  - client disconnect during fetch → return 499 (no body).
  - upstream non-ok → log structured (status + url + 256-char preview
    server-side) + return sanitized JSON to the browser. Backend body
    text is never echoed.

### 3. copilotkit route runtime hints (S-2)

`frontend/src/app/api/copilotkit/route.ts`:

- Same `runtime`/`dynamic`/`fetchCache` exports.
- Renamed the module-scope `CopilotRuntime` instance to `copilotRuntime`
  so it doesn't collide with the `runtime` segment-config export.

### 4. Regression test for the validator (FE-SEC test policy)

`frontend/__tests__/agent-proxy-validation.test.ts` — 20 assertions
covering non-object rejection, field-type rejection, well-formed-body
acceptance, `resolveSessionId` precedence + camelCase, and a regression
case stating "malformed body never reaches upstream fetch".

`npm test` runs all tests via Node 25's built-in `node:test` with
`--experimental-strip-types` — no new devDependency.

`tsconfig.json`: added `allowImportingTsExtensions: true` so the
`.ts`-extension import that Node strip-types requires also passes
`tsc --noEmit`.

### 5. CSRF posture documentation (SEC-1 disposition)

`frontend/docs/csrf.md` — explicit v1 acceptance rationale plus the
triggers that require closing the gate in v2 (multi-origin, multi-user,
real auth). Cross-references SEC-1..SEC-4 and the v2 AUTH-01..N
requirements.

## Tests Added

Test suite: `frontend/__tests__/agent-proxy-validation.test.ts`
Test runner: `node --test --experimental-strip-types --no-warnings __tests__/*.test.ts`

```
✔ rejects non-object input
✔ accepts empty object (all fields optional)
✔ rejects messages not array
✔ rejects message without role
✔ rejects message with non-string role
✔ rejects message with non-string id
✔ rejects state as array
✔ rejects forwardedProps as string
✔ rejects threadId as number
✔ rejects runId as object
✔ rejects tools as object
✔ rejects context as string
✔ accepts a well-formed body
✔ resolveSessionId picks from properties first
✔ resolveSessionId falls back to forwardedProps
✔ resolveSessionId accepts camelCase sessionId in forwardedProps
✔ resolveSessionId returns null when absent everywhere
✔ ensureMessageIds synthesises missing ids
✔ ensureMessageIds handles undefined input
✔ regression: malformed body must not reach upstream — full reject path

tests 20 / pass 20 / fail 0
```

This is the FE-SEC regression test. Per the project test policy
(bugfixes ship with a regression test that would have caught them), this
would catch a future regression where the validator accepts a malformed
body and the proxy silently forwards it upstream.

## CSRF Posture

See `frontend/docs/csrf.md`. Tl;dr: single-operator PoC on a Docker
Compose stack with no cross-origin surface; explicitly documented as v1
acceptance with the four triggers that require multi-origin hardening
documented for the v2 milestone.

## Verification

- `cd frontend && npx tsc --noEmit` — clean.
- `cd frontend && npm test` — 20/20 pass.
- `cd frontend && npm run build` — succeeded.

## Deviations from Plan

None — all 5 tasks executed as planned. One mid-task fix: the
`export const runtime = "nodejs"` segment-config export collided with a
local `runtime` const for `CopilotRuntime` in copilotkit/route.ts;
renamed the local to `copilotRuntime` (Rule 1 - blocking issue, fixed
inline as part of task 3).

## Commits

- `bdc3d9db5` docs(06-02): plan FE-ROUTES + FE-SEC
- `3af116a4d` feat(06-02): extract agent-proxy validator into pure module (SEC-2, FE-SEC)
- `3587ec468` fix(06-02): agent-proxy SSE lifecycle + input-trust + debug-gate (S-2, S-3, S-4, SEC-2, SEC-3)
- `3eb1283b6` fix(06-02): add runtime/dynamic/fetchCache exports to copilotkit/route (S-2)
- `7e8015b49` test(06-02): regression test for agent-proxy input-trust validator (FE-SEC)
- `2cb3cae86` docs(06-02): document CSRF posture for v1 deployment (SEC-1)

## Self-Check: PASSED

- FOUND: `frontend/src/app/api/agent-proxy/validation.ts`
- FOUND: `frontend/__tests__/agent-proxy-validation.test.ts`
- FOUND: `frontend/docs/csrf.md`
- FOUND: commits `bdc3d9db5`, `3af116a4d`, `3587ec468`, `3eb1283b6`, `7e8015b49`, `2cb3cae86` in `git log`
