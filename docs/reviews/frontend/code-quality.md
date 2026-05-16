# Code Quality

DRY, dead code, type safety, naming, and small structural issues.

---

## CQ-1 — Three unused modules kept as "fallback" *(medium)*

**Files:** `src/components/ChatWindow.tsx`, `src/components/Composer.tsx`, `src/lib/agui-http-agent.ts`, `src/utils/sseParser.ts`

Each file's leading comment admits it's not on the active path. They still ship to the bundle (or at least to the type-check / lint surface) and create the false impression they're load-bearing — the next refactor wastes time understanding them.

**Fix:** Delete. If the patterns are documented elsewhere (research notes, `docs/`), reference those instead. "Kept for future use" is the canonical anti-pattern — bring it back from git when actually needed.

---

## CQ-2 — `AGENT_NAME` duplicated *(low)*

**Files:** `src/app/page.tsx:15-16`, `src/components/AgentBody.tsx:19-20`

```ts
const AGENT_NAME = process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent";
```

Defined identically in two places. The default string `"kloc_agent"` also appears in `src/app/api/copilotkit/route.ts:20` and `src/app/api/agent-proxy/route.ts:33` with a *different* env var name (`COPILOTKIT_AGENT_NAME` vs `NEXT_PUBLIC_COPILOTKIT_AGENT_NAME`). If they diverge, the page and the runtime won't agree on the agent name.

**Fix:** One `src/lib/config.ts` module exporting `AGENT_NAME` (client) and a server twin if needed. Document the precedence between the two env vars (typically: server reads the non-public, client reads `NEXT_PUBLIC_*` which falls back to the server one at build time).

---

## CQ-3 — Backend URL default repeated *(low)*

**Files:** `src/lib/api.ts:7-8`, `src/lib/agui-http-agent.ts:11-12`, `src/app/api/agent-proxy/route.ts:28-31`, `next.config.ts:7`

`"http://localhost:8000"` appears as a fallback in five places. Centralize in `src/lib/config.ts`.

---

## CQ-4 — Magic string `"__new__"` as a sentinel *(low)*

**File:** `src/app/page.tsx:59, 174`

```ts
setBusyId("__new__");
{busyId === "__new__" ? "Starting…" : "+ Start new chat"}
```

Fragile and easy to typo silently. Use a discriminated union:

```ts
type BusyState = { kind: "none" } | { kind: "new" } | { kind: "pick"; id: string };
```

---

## CQ-5 — `Status | string` union widens the type to `string` *(low)*

**File:** `src/components/ToolCallCard.tsx:11, 36`

```ts
type Status = "inProgress" | "executing" | "complete";
...
status: Status | string;
```

`Status | string` collapses to `string` — the literal union provides no narrowing benefit. Either drop `Status` entirely, or constrain to `Status` and centralize the conversion at the boundary.

---

## CQ-6 — `IncomingMessage` index signature swallows fields *(low)*

**File:** `src/app/api/agent-proxy/route.ts:35-42`

```ts
type IncomingMessage = {
  id?: string;
  role: string;
  content?: unknown;
  [key: string]: unknown;
};
```

The index signature defeats type-checking on every other field, and `role: string` accepts arbitrary strings. Either define the full AG-UI message variants (User/Assistant/Tool/System) and use a discriminated union, or import the type from `@ag-ui/client` if it's public.

Same comment applies to `IncomingBody.state: Record<string, unknown>` and `forwardedProps` — fine as boundary types, but at least narrow `role` to the runtime's accepted set so a typo at the caller is caught here, not silently forwarded.

---

## CQ-7 — `apiRoleToGqlRole` returns wrong type for "tool" historically *(low)*

**File:** `src/components/AgentBody.tsx:26-34`

```ts
if (role === "tool") return Role.Assistant;
```

The comment explains the intent (tool rows render as assistant content) but the resulting `TextMessage` loses the original `role`, so a future "show tool messages distinctly" feature can't recover it from `messages`. Consider keeping `Role.Tool` and gating rendering on the type if CopilotKit's runtime tolerates it; otherwise tag the resulting message with a non-role marker (custom field on metadata) so the origin survives the round-trip.

---

## Style / convention nits (not separate issues)

- `next.config.ts:6-8` — `env: { NEXT_PUBLIC_BACKEND_URL: ... }` re-exports a value that's *already* public-by-prefix; the block is redundant.
- `src/components/ToolCallCard.tsx:75-90` and `:91-109` — duplicated `<details><pre>…</pre></details>` blocks. Extract `<CollapsibleJson label={...} value={...} />`.
- `src/app/page.tsx:178-182` — error rendering uses raw color `"crimson"`; align with the rest of the rgba palette (or, better, a CSS class).
