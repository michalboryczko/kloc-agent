---
phase: 6
plan: 04
subsystem: frontend
tags: [frontend, code-quality, dry, type-safety, cleanup, eslint]
requires: [06-01, 06-02, 06-03]
provides:
  - Centralized BROWSER_BACKEND_URL fallback
  - BusyState discriminated union replacing the "__new__" sentinel
  - Status type widening removed from ToolCallCard
  - IncomingMessage index signature dropped (validator now owns the trust boundary)
  - CollapsibleJson sub-component (deduplicates args + result <details> blocks)
  - eslint.config.mjs migrated to native flat config (no more FlatCompat)
  - tsc --noEmit clean + eslint clean across frontend/src + frontend/__tests__
affects:
  - frontend/src/lib/config.ts
  - frontend/src/lib/api.ts
  - frontend/src/app/page.tsx
  - frontend/src/components/SessionPicker.tsx
  - frontend/src/components/ToolCallCard.tsx
  - frontend/src/app/api/agent-proxy/validation.ts
  - frontend/next.config.ts
  - frontend/eslint.config.mjs
tech-stack:
  added: []
  patterns:
    - "Discriminated unions over string sentinels for state machines"
    - "Validator-owned trust boundary => downstream types can be strict"
key-files:
  modified:
    - frontend/src/lib/config.ts
    - frontend/src/lib/api.ts
    - frontend/src/app/page.tsx
    - frontend/src/components/SessionPicker.tsx
    - frontend/src/components/ToolCallCard.tsx
    - frontend/src/app/api/agent-proxy/validation.ts
    - frontend/next.config.ts
    - frontend/eslint.config.mjs
decisions:
  - "Keep agent-proxy/route.ts's BACKEND_URL fallback inline (BACKEND_URL > NEXT_PUBLIC_BACKEND_URL > localhost) rather than importing from src/lib/config — server-side and client-side env-precedence differ, and a server-side import would couple route handlers to the client config module."
  - "Migrate eslint.config.mjs to native flat config. FlatCompat + ESLint 9.39 + eslint-config-next 16.0.8 produces a circular-reference crash during config validation; the native flat exports of eslint-config-next (`./core-web-vitals`, `./typescript`) work cleanly."
metrics:
  completed: 2026-05-16
---

# Phase 6 Plan 04: IncomingBody tightening + DRY + final gate Summary

One-liner: Replace string sentinels with discriminated unions, deduplicate
`<details>` rendering, tighten message-shape types now that the validator
owns the trust boundary, and migrate eslint config to flat-native so the
linter actually runs.

## What Changed

### 1. Centralized BROWSER_BACKEND_URL (CQ-3)

`src/lib/config.ts` gained `BROWSER_BACKEND_URL`; `src/lib/api.ts`
imports it. Removed the redundant `env: { NEXT_PUBLIC_BACKEND_URL: ... }`
block from `next.config.ts` — `NEXT_PUBLIC_*` vars are auto-inlined.
Down from 3 fallback sites to 2 (one client, one server with different
env precedence).

### 2. BusyState discriminated union (CQ-4)

```ts
type BusyState =
  | { kind: "none" }
  | { kind: "new" }
  | { kind: "pick"; id: string };
```

Replaces the `busyId: "__new__" | string | null` pattern in both
`page.tsx` and `SessionPicker.tsx`. A typo at any callsite now fails
type-check instead of silently mis-matching.

### 3. Status type widening removed (CQ-5)

`ToolCallCard.tsx` previously declared `status: Status | string` which
TypeScript collapses to `string` (no narrowing benefit). Dropped the
union and the local `Status` alias; prop type is now plainly `string`.
Display logic uses equality checks for the narrowing it needs.

### 4. IncomingMessage index signature dropped (CQ-6)

```ts
// before
export type IncomingMessage = {
  id?: string;
  role: string;
  content?: unknown;
  [key: string]: unknown;  // ← removed
};
```

Now that `validateIncomingBody` in `validation.ts` (Plan 06-02) enforces
shape at the trust boundary, the index signature is dead weight. JS
spread semantics still pass through any extra fields the AG-UI envelope
hasn't typed.

### 5. CollapsibleJson sub-component (style nit from code-quality.md)

`ToolCallCard.tsx` had two near-identical `<details><summary>…<pre>…</pre></details>`
blocks for arguments and result. Extracted a module-scope
`<CollapsibleJson label value defaultOpen />` and replaced both
sites. Single source of formatting and chevron-rotation markup.

### 6. ESLint flat-config migration (final gate enabler)

`eslint.config.mjs` previously used `FlatCompat` to extend
`next/core-web-vitals` and `next/typescript`. With ESLint 9.39 +
eslint-config-next 16.0.8 this combination produced a circular-reference
`JSON.stringify` crash during config-schema validation — meaning *no
rule ever ran* against the project. Migrated to native flat-config
imports:

```mjs
import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const eslintConfig = [
  ...coreWebVitals,
  ...typescript,
  { ignores: [".next/**", "node_modules/**"] },
];

export default eslintConfig;
```

`npx eslint src/ __tests__/` now exits 0 with no findings.

## Type Tightening

| Type | Before | After |
|------|--------|-------|
| `busyId` | `string \| null` with `"__new__"` magic | `BusyState` discriminated union |
| `ToolCallCard.status` | `Status \| string` (widens to string) | `string` (no false narrowing) |
| `IncomingMessage` | `{...; [key: string]: unknown }` (open) | `{ id?: string; role: string; content?: unknown }` (closed, validator-enforced) |

## DRY Refactors

| Duplicate | Sites before | After |
|-----------|--------------|-------|
| `"http://localhost:8000"` fallback | 3 (lib/api.ts, agent-proxy/route.ts, next.config.ts) | 2 (lib/config.ts client, agent-proxy/route.ts server — different env precedence) |
| `AGENT_NAME = process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent"` | 2 (page.tsx, SessionRail.tsx) | 1 (lib/config.ts, imported by both) — resolved in 06-01 |
| `<details><summary>...<pre>...</pre></details>` for JSON dump | 2 (args + result in ToolCallCard) | 1 `CollapsibleJson` component, 2 usages |

## Final Gate

| Check | Status |
|-------|--------|
| `cd frontend && npx tsc --noEmit` | clean (0 errors) |
| `cd frontend && npx eslint src/ __tests__/` | clean (0 errors, 0 warnings) |
| `cd frontend && npm test` | 25/25 passing across 2 test files |
| `cd frontend && npm run build` | succeeded, all 3 static pages generated |
| `uv run python -m pytest tests/unit/ -q` | 136 passed, 1 skipped (Postgres-unreachable test, pre-existing) |

## Deviations from Plan

**Rule 1 - Bug:** ESLint refused to run at all with the existing
`FlatCompat`-based config (pre-existing tooling rot, not introduced by
this plan). Per Rule 1, fixed inline by migrating to native flat config.
Captured in commit `8636868b9` and documented above.

**Rule 3 - Blocking issue:** Node 25's strip-types loader requires
explicit `.ts` extensions; `src/lib/api.ts`'s `import { ... } from "./config"`
broke the `api-errors.test.ts` test runner. Added `.ts` extension to the
import; Next 16 Turbopack handles both forms. Verified by clean build.
Captured in commit `e8433d379`.

## Commits

- `32bf041f4` docs(06-04): plan FE-QUALITY
- `f89ea4395` refactor(06-04): centralize BROWSER_BACKEND_URL in lib/config (CQ-3)
- `f8dc5088c` refactor(06-04): replace busyId magic string with discriminated union (CQ-4)
- `97bab68ca` refactor(06-04): tighten ToolCallCard status type + extract CollapsibleJson (CQ-5, style nit)
- `e8433d379` refactor(06-04): drop IncomingMessage index signature (CQ-6)
- `8636868b9` chore(06-04): migrate eslint.config.mjs to native flat config (FE-QUALITY)

## Self-Check: PASSED

- FOUND: all modified files
- FOUND: commits `32bf041f4`, `f89ea4395`, `f8dc5088c`, `97bab68ca`, `e8433d379`, `8636868b9` in `git log`
- Final-gate matrix above all green.
