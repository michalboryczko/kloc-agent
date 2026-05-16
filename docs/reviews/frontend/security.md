# Security

Issues across input trust, log hygiene, CSRF, and request authentication.

---

## SEC-1 — `/api/agent-proxy` has no authentication or origin check *(medium)*

**File:** `src/app/api/agent-proxy/route.ts:85-147`

The route accepts any POST that names a `session_id` and forwards it to the FastAPI backend with `Accept: text/event-stream`. There is:

- **No auth header / cookie check** before the upstream call.
- **No origin/`Sec-Fetch-Site` check**, so a CSRF-style cross-site POST could initiate runs as the logged-in user (cost the operator real LLM calls and arbitrary tool execution).
- **No rate limiting**: a single client can spin up unbounded concurrent SSE streams.

This is acceptable *only* if the backend enforces authz on `/v1/sessions/{id}/stream` and `session_id` is treated as a capability (unguessable + per-user). Verify:

1. Is `session_id` minted server-side with sufficient entropy? (`createSession()` in `lib/api.ts:61` returns whatever the backend gives — confirm it's a v4 UUID or stronger.)
2. Does the backend reject a session id that belongs to a different user / org?

**Fix:** Forward the user's session cookie (`cookies()` from `next/headers`) to the backend, and have the backend authenticate. Add a same-origin check before forwarding (`req.headers.get("origin")` against `req.nextUrl.origin`).

---

## SEC-2 — `forwardedProps` are passed through unfiltered *(low–medium)*

**File:** `src/app/api/agent-proxy/route.ts:122-130`

```ts
forwardedProps: { ...(body.forwardedProps ?? {}), session_id: sessionId },
```

Any caller-supplied keys land in `forwardedProps` and reach the backend (and, depending on backend design, the LLM/tool layer). If a downstream consumer trusts `forwardedProps.user_role` or similar, a malicious client can lie about it.

**Fix:** Whitelist the keys the backend actually needs and drop the rest. Document the contract in a `// Allowed forwardedProps:` comment block.

---

## SEC-3 — Diagnostic logging leaks request bodies *(medium — also in `server-and-routes.md`)*

**File:** `src/app/api/agent-proxy/route.ts:99-104`

Cross-referenced as **S-3** in `server-and-routes.md`. Logging full `state` and `forwardedProps` objects is a privacy / secret-leak risk in production log aggregators.

---

## SEC-4 — `NEXT_PUBLIC_BACKEND_URL` baked into client bundle *(low — informational)*

**Files:** `src/lib/api.ts:7-8`, `src/lib/agui-http-agent.ts:11-12`, `next.config.ts:6-8`

Internal hostnames embedded in the client bundle are a soft information-disclosure. Currently `http://localhost:8000` is the default which is fine for local dev, but verify production builds substitute a public-facing host and never expose internal-only DNS.

Additionally, the browser hits the backend directly from `src/lib/api.ts`, which:

- Requires CORS to be configured on the FastAPI side (operational burden).
- Bypasses Next.js' ability to act as an auth proxy.

Consider routing all backend calls through Next.js route handlers (as `/api/agent-proxy` already does for streaming). Then the public URL stays at the Next layer and CORS becomes a non-issue.
