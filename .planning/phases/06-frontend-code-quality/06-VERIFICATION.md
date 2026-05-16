---
phase: 06-frontend-code-quality
verified: 2026-05-16T20:47:03Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Picker → chat lazy-load smoke"
    expected: "On / (picker), DevTools Network shows no 1.5 MB CopilotKit chunk request; after clicking a session, the chunk loads and chat renders within ~1s without flicker or error overlay"
    why_human: "next/dynamic ssr:false behavior, network-tab inspection, and perceived loading UX cannot be asserted via grep or unit test"
  - test: "Chat hydration smoke after dynamic import"
    expected: "After selecting a session, CopilotKit + CopilotChat mount, the textarea has focus, the session header reads 'session <8-char id>…', and the back button returns to the picker preserving session list"
    why_human: "Visual hydration sequence and React render output of a client-only dynamic component is observable only at runtime"
  - test: "agent-proxy debug-flag behavior"
    expected: "With NEXT_PUBLIC_DEBUG_HTTP=true set, the server log emits 'agent-proxy] no session_id; keys=' with only Object.keys names; with the flag unset, no warn is emitted on the same malformed-shape input"
    why_human: "Requires running the server and inspecting stdout; env-driven log gating cannot be asserted by static analysis"
  - test: "Error surface differentiation (D-3)"
    expected: "With backend stopped, picker shows 'network unreachable — is the backend running?'; with backend returning 500, picker shows 'backend 500: <preview>'"
    why_human: "Requires runtime fault injection and visual confirmation of the differentiated user-facing string"
---

# Phase 6: Frontend code quality — Verification Report

**Phase Goal:** Resolve the 36 findings across the 7 `docs/reviews/frontend/` category files. Highest-leverage items first: CopilotRuntime hoist + CopilotKit lazy-load.
**Verified:** 2026-05-16T20:47:03Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | CopilotRuntime allocated once at module scope (not per request) in `src/app/api/copilotkit/route.ts`; CopilotKit + `<CopilotChat>` dynamic-imported on the chat page so session-picker route does not ship the chat bundle | VERIFIED | `copilotkit/route.ts:25` `const copilotRuntime = new CopilotRuntime(...)` at module scope; `:35` POST handler is one-line forwarder. `page.tsx:18-21` `const ChatShell = dynamic(() => import("@/components/ChatShell")..., { ssr: false })`. Bundle audit in 06-01-SUMMARY: 2,636,690 B → 66,299 B (97.5% reduction) on `/`. |
| SC2 | Server/route findings cleared on `agent-proxy/route.ts` and `copilotkit/route.ts` (SSE lifecycle, request scope, runtime allocation) | VERIFIED | Both routes declare `export const runtime = "nodejs"`, `dynamic = "force-dynamic"`, `fetchCache = "force-no-store"` (`copilotkit/route.ts:9-11`, `agent-proxy/route.ts:16-18`). Upstream fetch uses `AbortSignal.any([req.signal, AbortSignal.timeout(30_000)])` (`agent-proxy/route.ts:96-99`). |
| SC3 | Data-fetching findings cleared: `listSessions` / `listMessages` dedup, `AbortController` propagation on unmount/navigation, clear error surface | VERIFIED | All 5 `lib/api.ts` helpers accept `signal?: AbortSignal` and thread to `fetch`. `page.tsx:63-75` `useEffect` uses AbortController cleanup; `pickCtrlRef` ref-based dedup at `:77-82`, used in `pickExisting`/`startNew`/`onBack`. `ApiError` + `NetworkError` exported (`api.ts:59-82`); `formatError` differentiates (`page.tsx:42-51`). |
| SC4 | Security findings cleared: body-dump behind debug flag, CSRF posture documented, input-trust tightening on `agent-proxy/route.ts` body (with regression test) | VERIFIED | `agent-proxy/route.ts:57-65` gates `console.warn` behind `NEXT_PUBLIC_DEBUG_HTTP === "true"` and emits only `Object.keys(...)`. `frontend/docs/csrf.md` documents v1 posture + v2 triggers. `validation.ts` enforces structural body shape; `agent-proxy/route.ts:46-52` returns 400 on failure. 20-assertion regression suite at `__tests__/agent-proxy-validation.test.ts` (passes). |
| SC5 | Performance + code-quality findings cleared: inline objects memoized/hoisted, hook deps stabilised, `IncomingBody` type widening tightened; `tsc --noEmit` and `eslint` clean across `frontend/src/**` | VERIFIED | `RenderToolCall` module-scope in `AgentBody.tsx:15`. `INITIAL_AGENT_STATE`, `AGENT_NAME`, `BROWSER_BACKEND_URL` centralized in `lib/config.ts`. `SessionPicker.tsx:32` memoizes per-row `toLocaleString` via `useMemo`. `BusyState` discriminated union replaces `"__new__"` sentinel (no matches in `frontend/src/`). `Status \| string` widening removed from `ToolCallCard.tsx`. `IncomingMessage` index signature dropped (`validation.ts:3-7`). `tsc --noEmit` clean; `eslint src/ __tests__/` clean; `npm test` 25/25 pass; `npm run build` succeeds. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `frontend/src/app/api/copilotkit/route.ts` | CopilotRuntime at module scope; runtime/dynamic/fetchCache exports | VERIFIED | 36 lines; module-scope `copilotRuntime` const, segment-config exports present |
| `frontend/src/app/api/agent-proxy/route.ts` | Validates body, debug-gated warn, timeout, sanitized errors | VERIFIED | 173 lines; imports validator; AbortSignal.any with 30s timeout; structured error logging; sanitized responses |
| `frontend/src/app/api/agent-proxy/validation.ts` | Pure validator module | VERIFIED (new) | 113 lines; `validateIncomingBody`, `resolveSessionId`, `ensureMessageIds` |
| `frontend/src/components/ChatShell.tsx` | Houses chat-view markup; CSS co-located | VERIFIED (new) | 73 lines; imports CopilotKit/CopilotChat and `styles.css`; dynamic-loaded from page.tsx |
| `frontend/src/lib/config.ts` | Central client-side config: AGENT_NAME, BROWSER_BACKEND_URL, INITIAL_AGENT_STATE | VERIFIED (new) | 16 lines; three exports, env precedence preserved |
| `frontend/src/lib/api.ts` | AbortSignal threading + ApiError/NetworkError | VERIFIED | All 5 helpers accept `signal`; classes exported; `safeFetch` wraps fetch |
| `frontend/src/app/page.tsx` | Dynamic import, AbortController dedup, formatError, BusyState | VERIFIED | All four patterns present; no `"__new__"` |
| `frontend/__tests__/agent-proxy-validation.test.ts` | 20 regression assertions | VERIFIED (new) | Passes via `npm test` |
| `frontend/__tests__/api-errors.test.ts` | Error-class smoke | VERIFIED (new) | 5 assertions pass |
| `frontend/docs/csrf.md` | v1 CSRF posture | VERIFIED (new) | 85 lines; covers acceptance + v2 triggers |
| `frontend/eslint.config.mjs` | Flat-native config that actually runs | VERIFIED | Migrated off FlatCompat; `npx eslint src/ __tests__/` exits 0 |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `page.tsx` | `ChatShell.tsx` | `next/dynamic` (ssr:false) | WIRED | `page.tsx:18-21`; mounts only when `picked !== null` (`page.tsx:144-149`) |
| `agent-proxy/route.ts` | `validation.ts` | named import | WIRED | Imports `ensureMessageIds`, `resolveSessionId`, `validateIncomingBody`; calls validator on `:46` before any upstream call |
| `page.tsx` | `lib/api.ts` (`AbortSignal`) | options-object `signal` | WIRED | `listSessions({ signal: ctrl.signal })` `:66`; `listMessages(s.id, { ..., signal })` `:91`; `createSession({ signal })` `:112` |
| `page.tsx` | `ApiError` / `NetworkError` | `instanceof` in `formatError` | WIRED | `:42-51` differentiates on `ApiError` and `NetworkError`; result used by `setError(formatError(e))` |
| `ChatShell.tsx` | `@copilotkit/react-ui/styles.css` | side-effect import | WIRED | Co-located with the lazy chunk; removed from `layout.tsx` |
| `AgentBody.tsx` | `RenderToolCall` (module-scope) | `useCopilotAction({ render: RenderToolCall })` | WIRED | `:33` |
| `SessionRail.tsx` | `INITIAL_AGENT_STATE` | `useCoAgent({ initialState })` | WIRED | `:42-44` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `page.tsx` (picker) | `sessions` | `listSessions({ signal })` → backend `/v1/sessions` | Yes (REST round-trip; AbortError handled) | FLOWING |
| `page.tsx` (pick handler) | `picked.initialMessages` | `listMessages(id, { signal })` → backend | Yes | FLOWING |
| `ChatShell` | `sessionId`, `initialMessages` | props from `page.tsx` after pick | Yes (only mounted when `picked !== null`) | FLOWING |
| `agent-proxy/route.ts` | upstream SSE body | validated POST → backend `/v1/sessions/{id}/stream` | Yes (forwarded verbatim when 2xx) | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Frontend type-check | `cd frontend && npx tsc --noEmit` | exit 0, no output | PASS |
| Frontend lint | `cd frontend && npx eslint src/ __tests__/` | exit 0, no output | PASS |
| Frontend tests | `cd frontend && npm test` | 25/25 pass | PASS |
| Frontend build | `cd frontend && npm run build` | succeeded, 3 static pages, agent-proxy + copilotkit listed as `ƒ` dynamic | PASS |
| Backend regression | `uv run python -m pytest tests/unit/ -q` | 136 passed, 1 skipped (pre-existing Postgres-unreachable) | PASS |
| BusyState sentinel removed | `grep -rn '"__new__"' frontend/src/` | 0 matches | PASS |
| Backend URL fallback dedup | `grep -rn '"http://localhost:8000"' frontend/src/ frontend/next.config.ts` | 2 matches (lib/config.ts client, agent-proxy/route.ts server — different env precedence) | PASS |
| Status type widening removed | `grep -n 'Status' frontend/src/components/ToolCallCard.tsx` | no matches | PASS |

### Probe Execution

Phase 6 declares no `scripts/*/tests/probe-*.sh` files and is not a migration/tooling phase. Step 7c does not apply.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| FE-PERF | 06-01 | All 7 findings in `performance.md` resolved | SATISFIED | RenderToolCall module-scope, INITIAL_AGENT_STATE hoisted, SessionPicker per-row formatting memoized, hook deps stabilised |
| FE-BUNDLE | 06-01 | CopilotRuntime hoisted; CopilotKit dynamic-imported | SATISFIED | Bundle audit shows 97.5% reduction on `/` route; runtime allocated once at module scope |
| FE-ROUTES | 06-02 | 5 findings in `server-and-routes.md` resolved | SATISFIED | runtime/dynamic/fetchCache exports on both routes; 30s timeout; sanitized error surface |
| FE-DATA | 06-03 | 4 findings in `data-fetching.md` resolved | SATISFIED | AbortSignal threaded through api.ts; useEffect AbortController cleanup; pickCtrlRef dedup; ApiError/NetworkError differentiation |
| FE-SEC | 06-02 | 4 findings in `security.md` resolved with regression test | SATISFIED | Debug-gated body dump; CSRF posture documented; input-trust validator + 20-assertion regression test |
| FE-QUALITY | 06-04 | 7 findings in `code-quality.md` resolved | SATISFIED | BROWSER_BACKEND_URL centralized; BusyState union replaces sentinel; Status widening dropped; IncomingMessage index signature dropped; CollapsibleJson extracted; ESLint flat-native config; tsc/eslint clean |

All declared requirement IDs (FE-PERF, FE-BUNDLE, FE-ROUTES, FE-DATA, FE-SEC, FE-QUALITY) are satisfied by code evidence. No orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (none) | — | — | — | No TBD/FIXME/XXX markers, no `return null` stubs, no hardcoded-empty props, no console.log-only handlers detected in modified files |

Anti-pattern scan against modified files in Phase 6 commits returned clean. The only `console.warn` in `agent-proxy/route.ts` is debug-gated (Step 7c criteria require debt-marker references but no unreferenced debt markers were found).

### Human Verification Required

#### 1. Picker → chat lazy-load smoke

**Test:** Start the frontend (`npm run dev`), open `/`, open DevTools Network tab; click a session.
**Expected:** Before click, no chunk containing `@copilotkit/*` is requested (picker bundle audit claims 97.5% reduction). After click, the CopilotKit chunk loads, ChatShell mounts, no error overlay.
**Why human:** `next/dynamic` runtime behavior + Network panel inspection requires a browser session.

#### 2. Chat hydration smoke after dynamic import

**Test:** From picker, click an existing session; observe header + chat textarea + session subtitle.
**Expected:** Header reads "kloc agent BETA", session subtitle reads "session <8-char>…", CopilotChat textarea is focusable, back button returns to picker preserving session list.
**Why human:** Visual hydration sequence cannot be verified statically.

#### 3. agent-proxy debug-flag behavior

**Test:** Run frontend with `NEXT_PUBLIC_DEBUG_HTTP=true` then again unset; POST a body to `/api/agent-proxy` missing `session_id`.
**Expected:** With flag on, server logs `[agent-proxy] no session_id; keys= {body: [...], state: [...], forwardedProps: [...]}` (key names only, no values). With flag unset, no warn.
**Why human:** Env-driven log gating requires running the server and inspecting stdout.

#### 4. Error surface differentiation (D-3)

**Test:** Stop the backend → reload picker. Then start backend → 500 the `/v1/sessions` endpoint (e.g., temporarily break DB).
**Expected:** With backend down: "network unreachable — is the backend running?". With 500: "backend 500: <preview>".
**Why human:** Visual confirmation of the rendered error string requires browser + backend fault injection.

### Gaps Summary

No code-level gaps. All 5 Success Criteria are observably true in the codebase. All declared requirements satisfied with evidence. All 25 automated tests pass; tsc clean; eslint clean; build clean; backend regression suite passes.

Status is `human_needed` (not `passed`) because Phase 6's test policy explicitly calls for a manual smoke check on the chat view + lazy-load UX + runtime-only behaviors (debug-flag log gating, differentiated error strings). These are runtime-visual concerns that no automated check can confirm.

---

_Verified: 2026-05-16T20:47:03Z_
_Verifier: Claude (gsd-verifier)_
