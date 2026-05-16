# Frontend Code Review — kloc-agent

**Date:** 2026-05-16
**Scope:** `frontend/src/**` (11 files) — Next.js 16 + React 19 + CopilotKit 1.56.5 + AG-UI 0.0.42
**Lens:** Vercel React/Next.js best practices + general code review (security, a11y, type safety, DX)

## Summary

The frontend is small, well-commented, and structurally sound: a single page that picks a session and hands it to a `CopilotSidebar` with an AG-UI proxy in between. Most issues are **stylistic and preventative** rather than bugs — there are no critical correctness defects. The biggest pragmatic wins are:

1. **Hoist the CopilotKit runtime** out of the per-request POST handler (`app/api/copilotkit/route.ts:31`) — currently allocated on every request.
2. **Lazy-load CopilotKit** on the chat page so the session picker doesn't ship a chat bundle it never uses (`app/page.tsx:115`).
3. **Move inline styles to CSS modules / `globals.css`** — almost every component re-creates large style objects each render.
4. **Delete the three unused modules** (`Composer.tsx`, `ChatWindow.tsx`, `lib/agui-http-agent.ts`, `utils/sseParser.ts`) — they are self-documented as dead/fallback code.

No issue is blocking. Triage suggestions below.

## Categorized Issue Files

| File | Theme | Count | Severity range |
|------|-------|-------|----------------|
| [performance.md](./performance.md) | Re-render / inline objects / hook stability | 7 | low–medium |
| [bundle-and-loading.md](./bundle-and-loading.md) | Code splitting, dynamic imports | 4 | medium |
| [server-and-routes.md](./server-and-routes.md) | Next.js route handlers, SSE, request scope | 5 | medium |
| [data-fetching.md](./data-fetching.md) | Fetch lifecycle, dedup, abort | 4 | low–medium |
| [accessibility.md](./accessibility.md) | Labels, contrast, semantic markup | 5 | low–medium |
| [security.md](./security.md) | CSRF, log hygiene, input trust | 4 | low–medium |
| [code-quality.md](./code-quality.md) | Dead code, DRY, type widening | 7 | low |

## Recommended Triage Order

1. **Now (1 PR):** delete dead modules; hoist CopilotRuntime; move console.warn body dump behind a debug flag.
2. **Next (1 PR):** dynamic-import `CopilotKit`/`CopilotSidebar`; extract picker styles to a stylesheet; add `aria-label` to picker buttons and Composer textarea.
3. **Later:** consider SWR for `listSessions`/`listMessages`; add `useTransition` for picker→chat handoff; tighten `IncomingBody` types in `agent-proxy`.

## Files Reviewed

```
src/app/layout.tsx
src/app/page.tsx
src/app/api/agent-proxy/route.ts
src/app/api/copilotkit/route.ts
src/components/AgentBody.tsx
src/components/ChatWindow.tsx
src/components/Composer.tsx
src/components/ToolCallCard.tsx
src/lib/api.ts
src/lib/agui-http-agent.ts
src/utils/sseParser.ts
```

Also reviewed: `next.config.ts`, `package.json`.
