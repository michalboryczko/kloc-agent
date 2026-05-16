# CSRF Posture — kloc-agent frontend

**Status (v1, hardening milestone):** accepted as low-risk. No CSRF token,
no origin check, no per-request auth on the agent-proxy or copilotkit
routes. This is intentional for the current deployment shape and tracked
explicitly so the next milestone scoper can revisit it.

## Why this is acceptable in v1

The product today is a single-operator PoC. The deployment shape is:

- One operator on one workstation.
- A Docker Compose stack with three services — `backend` (FastAPI),
  `frontend` (Next.js), and per-session runner containers spawned by the
  backend on demand.
- The browser hits the Next.js server which proxies to FastAPI on the same
  Docker network. There is no external multi-tenant traffic, no external
  user identities, no shared session state across operators.
- Backend `/v1/*` endpoints are intentionally unauthenticated; AUTH-01..N
  is on the v2 milestone (see `.planning/REQUIREMENTS.md` "Out of Scope").
- The `HARDCODED_ANALYST_ID = "analyst-poc"` is the canonical reminder
  that this is single-operator.

In that deployment shape there is no "cross-site" surface to attack with a
CSRF style payload — there is no other origin a victim browser could be
sitting on while authenticated to the kloc-agent backend.

## What changes if this deployment goes multi-origin

The four findings catalogued in `docs/reviews/frontend/security.md` —
SEC-1 (no auth/origin/rate-limit on agent-proxy), SEC-2 (forwardedProps
pass-through), SEC-3 (body-dump log hygiene, now debug-gated), SEC-4
(backend URL in client bundle) — all assume the v1 single-origin posture.
The moment any of the following becomes true, the gate has to close:

1. The Next.js frontend is served from a different origin than the
   FastAPI backend (e.g. `app.kloc.example` and `api.kloc.example`).
2. The frontend is reachable by multiple authenticated users / orgs.
3. The backend grows real auth (cookies, bearer tokens, sessions).
4. Session ids stop being effectively per-operator.

When any of those triggers fires, **before merging the change**, the next
milestone must:

- Add an explicit same-origin / `Sec-Fetch-Site` check to
  `frontend/src/app/api/agent-proxy/route.ts` before forwarding upstream.
- Forward an auth header / cookie from the browser to the FastAPI
  backend (use `cookies()` from `next/headers`).
- Tighten `forwardedProps`: whitelist the keys the backend actually
  needs and drop the rest before forwarding.
- Add rate limiting on `/api/agent-proxy` and on the upstream stream
  endpoint.
- Decide whether the backend URL belongs in the client bundle
  (`NEXT_PUBLIC_BACKEND_URL`) or should be hidden behind Next.js route
  handlers (current direction in `src/lib/api.ts` already routes
  through `/api/agent-proxy` for streaming; the JSON endpoints in
  `src/lib/api.ts` still go direct).

## Mitigations already in place (v1)

These have already been applied during the FE-SEC plan (Phase 6 Plan 02):

- **Body-dump log hygiene** — the diagnostic `console.warn` in
  `agent-proxy/route.ts` is gated behind `NEXT_PUBLIC_DEBUG_HTTP === "true"`
  and only logs `Object.keys(...)` (no values). Default off in production.
- **Input trust gate** — the proxy validates the request body shape via
  `validateIncomingBody` in `src/app/api/agent-proxy/validation.ts`
  before any upstream fetch. Malformed bodies return 400. Regression
  test: `frontend/__tests__/agent-proxy-validation.test.ts`.
- **SSE lifecycle** — both `agent-proxy/route.ts` and `copilotkit/route.ts`
  declare `runtime = "nodejs"`, `dynamic = "force-dynamic"`,
  `fetchCache = "force-no-store"` so Next 16 cannot cache them or
  downgrade to Edge. Upstream fetch uses
  `AbortSignal.any([req.signal, AbortSignal.timeout(30_000)])` so a
  client disconnect or backend hang frees the Node worker.
- **Sanitized error surface** — backend body text is never echoed back to
  the browser; the route logs the upstream status + URL server-side and
  returns a stable error JSON to the client.

## References

- `docs/reviews/frontend/security.md` (SEC-1..SEC-4)
- `docs/reviews/frontend/server-and-routes.md` (S-1..S-5)
- `.planning/REQUIREMENTS.md` "v2 Requirements / Authentication & Multi-Tenant Surface"
