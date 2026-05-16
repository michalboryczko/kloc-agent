# Server & Route Handlers

Issues in `src/app/api/**` — the CopilotKit runtime endpoint and AG-UI proxy.

---

## S-1 — CopilotRuntime + agent map allocated per request *(medium)*

**Rule:** `server-hoist-static-io`
**File:** `src/app/api/copilotkit/route.ts:31-47`

```ts
export const POST = async (req: NextRequest) => {
  const agents: Record<string, AbstractAgent> = {
    [AGENT_NAME]: new HttpAgent({ url: proxyUrl(req) }),
  };
  const runtime = new CopilotRuntime({ agents: agents as any });
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({ ... });
  return handleRequest(req);
};
```

`CopilotRuntime`, the `HttpAgent`, and `handleRequest` are rebuilt for every POST. Under concurrent users this is allocation + setup overhead repeated unnecessarily, and any internal state (caches, schedulers) is thrown away each request.

**Fix:** The only per-request value is `proxyUrl(req)`. Build the URL relative-only and hoist:

```ts
// module scope
const agent = new HttpAgent({ url: "/api/agent-proxy" }); // or absolute via env
const runtime = new CopilotRuntime({ agents: { [AGENT_NAME]: agent } as any });
const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
  runtime,
  serviceAdapter,
  endpoint: "/api/copilotkit",
});

export const POST = (req: NextRequest) => handleRequest(req);
```

If `HttpAgent` requires an absolute URL, derive a single value at module load from `process.env.NEXTAUTH_URL` / `VERCEL_URL`, not from each request.

---

## S-2 — No `runtime`/`dynamic` exports on SSE-producing routes *(medium)*

**Rule:** `server-after-nonblocking` (adjacent)
**Files:** `src/app/api/agent-proxy/route.ts`, `src/app/api/copilotkit/route.ts`

Neither route declares:

```ts
export const runtime = "nodejs";       // required by node:crypto in agent-proxy
export const dynamic = "force-dynamic"; // SSE must never be statically rendered
export const fetchCache = "force-no-store";
```

In dev this is benign, but Next 16 can elect to cache or pre-render route handlers more aggressively. `agent-proxy` returns a `ReadableStream` for SSE — being implicitly cached or being moved to the Edge runtime (`randomUUID` from `node:crypto` is Node-only) would silently break it.

**Fix:** Add the three exports above to both route files.

---

## S-3 — `console.warn` body-key dump *(medium — security/log hygiene)*

**File:** `src/app/api/agent-proxy/route.ts:99-104`

```ts
console.warn("[agent-proxy] no session_id; body keys=", Object.keys(body), {
  state: body.state,
  forwardedProps: body.forwardedProps,
});
```

Logs the raw `state` and `forwardedProps`. In a real deployment those can contain user-supplied text, API keys, or PII forwarded by misconfigured clients. Even the diagnostic intent is dangerous as a permanent log.

**Fix:** Gate behind `process.env.DEBUG_AGENT_PROXY` and at minimum log only the *keys*, not values:

```ts
if (process.env.DEBUG_AGENT_PROXY) {
  console.warn("[agent-proxy] no session_id; keys=", {
    body: Object.keys(body),
    state: Object.keys(body.state ?? {}),
    forwardedProps: Object.keys(body.forwardedProps ?? {}),
  });
}
```

---

## S-4 — Upstream errors swallowed without context *(low–medium)*

**File:** `src/app/api/agent-proxy/route.ts:137-161`

```ts
const upstream = await fetch(url.toString(), { ... });
if (!upstream.ok || !upstream.body) {
  const text = await upstream.text().catch(() => "");
  return new Response(`agent-proxy: backend rejected (${upstream.status}): ${text}`, ...);
}
```

- No timeout: a slow backend ties up a Node worker indefinitely.
- No logging of the upstream `status`/correlation id → harder to debug from server logs.
- Re-emits backend body text directly to the browser — if the backend returns a stack trace, it lands in the chat UI.

**Fix:** Wrap with `AbortSignal.timeout(30_000)` combined with `req.signal` via `AbortSignal.any([...])`. Log structured upstream failure server-side. Send a sanitized error to the client.

---

## S-5 — `as any` cast hides type drift *(low)*

**File:** `src/app/api/copilotkit/route.ts:39-40`

Acknowledged TODO. Track a follow-up tied to the 1.53+ upgrade so the cast doesn't outlive its reason. Add `// TODO(copilotkit-1.53+): remove cast` and link an issue.
