---
phase: 05
plan: 04
subsystem: frontend-ui
tags: [ui, dead-code, cleanup]
requires: [UI-P3, UI-P4, UI-P5]
provides: [clean frontend tree free of four orphaned modules]
affects: []
tech-stack:
  added: []
  patterns: []
key-files:
  created: []
  deleted:
    - frontend/src/components/ChatWindow.tsx
    - frontend/src/components/Composer.tsx
    - frontend/src/lib/agui-http-agent.ts
    - frontend/src/utils/sseParser.ts
decisions:
  - "Retained AgentBody.tsx (renders null, registers wildcard tool-call renderer). It is 33 lines and serves a single responsibility — keeping it factored out of page.tsx avoids burying a hook inside route code. The original UI-P6 brief said 'delete if unused'; it remains used."
metrics:
  duration: ~10min
  completed: 2026-05-16
---

# Phase 5 Plan 04: UI-P6 Dead-Code Deletion Summary

Four modules removed. Frontend tree now contains only files that the
production app references.

## Deleted Modules

| File                                        | Reason                                                  |
| ------------------------------------------- | ------------------------------------------------------- |
| `frontend/src/components/ChatWindow.tsx`    | Alternate inline-chat surface; page.tsx now uses `<CopilotChat>` directly inside the chat-pane grid cell. |
| `frontend/src/components/Composer.tsx`      | Bare controlled textarea; never imported anywhere.       |
| `frontend/src/lib/agui-http-agent.ts`       | Direct AG-UI HttpAgent factory; CopilotKit path doesn't use it. |
| `frontend/src/utils/sseParser.ts`           | Fallback SSE parser; `@ag-ui/client` handles streaming inside CopilotKit. |

## AgentBody Decision

AgentBody.tsx is **retained**. After 05-01 it is a single-purpose,
renders-null component that registers `useCopilotAction({ name: "*" })`
inside `<CopilotKit>` so streamed tool calls flow through `ToolCallCard`.
Inlining the hook into page.tsx is possible but would put a side-effect
hook directly in route code; the small dedicated file is the more
readable arrangement.

## Tasks Completed

| Task | Name                                                    | Commit  |
| ---- | ------------------------------------------------------- | ------- |
| 1+2  | Delete four modules + AgentBody decision (plan commit)  | 614aa48 |
| 3    | Final build + backend tests                             | (verification — no code change) |

## Deviations from Plan

**[Rule 1 — Atomic commit]** Tasks 1 (deletions) and 2 (AgentBody
decision) landed alongside the plan markdown in commit 614aa48 because
`git rm` had pre-staged the deletions before the plan was committed. The
result is one cohesive commit that matches the plan's small scope; no
behaviour change risk and easy to revert as a unit. No fix needed.

## Verification

- `grep -rn "ChatWindow\|Composer\|agui-http-agent\|sseParser" frontend/src/` — empty.
- `npm run build` — clean (5 routes, no TS errors).
- `uv run python -m pytest tests/unit/ -q` — **136 passed, 1 skipped** (skip is a Postgres connectivity check; not caused by this phase).
- `grep -rn 'style={{' frontend/src/{components,app}` — empty (no inline styles remain).

## Self-Check: PASSED

- VERIFIED DELETION: ChatWindow.tsx, Composer.tsx, agui-http-agent.ts, sseParser.ts
- FOUND: commit 614aa48
- VERIFIED: frontend build green, backend tests green
