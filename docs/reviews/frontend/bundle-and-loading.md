# Bundle Size & Loading

Code-split / lazy-load opportunities to keep the first paint small.

---

## B-1 — CopilotKit is eagerly imported on the picker route *(medium)*

**Rule:** `bundle-dynamic-imports`, `bundle-conditional`
**File:** `src/app/page.tsx:4-5`, `:115-132`

```tsx
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui";
```

Both packages (plus their pino logger transitive deps — see `next.config.ts:14`) are loaded synchronously at the top of `page.tsx`. Users on the session picker — the *initial* view — ship the entire CopilotKit runtime, CSS, and AG-UI client just to render a list of buttons.

**Fix:** Dynamically import the chat shell so the picker is a cheap shell:

```tsx
const ChatShell = dynamic(() => import("@/components/ChatShell"), { ssr: false });
```

Then `<ChatShell sessionId={picked.sessionId} initialMessages={picked.initialMessages} />` after the user picks. Also move `import "@copilotkit/react-ui/styles.css"` (`src/app/layout.tsx:2`) into that lazy-loaded component so its CSS isn't inlined into the picker render.

---

## B-2 — Preload chat shell on hover/focus *(low)*

**Rule:** `bundle-preload`
**File:** `src/app/page.tsx:210-241`

After B-1, fire `void import("@/components/ChatShell")` on `onMouseEnter` / `onFocus` of session-row buttons and the "Start new chat" button. The chat bundle starts streaming while the click animation completes, hiding the latency cost of dynamic import.

---

## B-3 — `serverExternalPackages` workaround is fragile *(low — informational)*

**File:** `next.config.ts:14-19`

```ts
serverExternalPackages: ["@copilotkit/runtime", "pino", "pino-pretty", "thread-stream"],
```

Treating `@copilotkit/runtime` as external means it must be present in `node_modules` at runtime. With `output: "standalone"` (`next.config.ts:4`), Next.js' file tracer needs to discover and copy `pino`'s native bits — verify they end up in `.next/standalone/node_modules` after `next build`. If anything is missing in prod, the route handler crashes only when first invoked.

**Action:** Add a smoke test that exercises `POST /api/copilotkit` against the built standalone bundle, not only dev mode.

---

## B-4 — No barrel-import audit on CopilotKit packages *(low)*

**Rule:** `bundle-barrel-imports`, `bundle-analyzable-paths`
**File:** `src/components/AgentBody.tsx:4-9`

```ts
import {
  useCoAgent,
  useCopilotAction,
  useCopilotMessagesContext,
} from "@copilotkit/react-core";
```

`@copilotkit/react-core` is a barrel. With Next.js' `optimizePackageImports`, it's automatically transformed *if* the package opts in via `"sideEffects": false`. Worth verifying with `next build --analyze` (or adding the package to `experimental.optimizePackageImports` in `next.config.ts`) — otherwise every consumer pulls the full module graph.

**Action:** Run `ANALYZE=true next build` once and check the client bundle for `@copilotkit/react-core` weight. Add to `optimizePackageImports` if the deep paths are usable.
