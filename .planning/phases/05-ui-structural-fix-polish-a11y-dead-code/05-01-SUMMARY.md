---
phase: 05
plan: 01
subsystem: frontend-ui
tags: [ui, layout, copilotkit, structural]
requires: [UI-P2 — chat-view grid established in page.tsx]
provides: [SessionRail component, inline CopilotChat layout, F-1 fix]
affects: [page.tsx, AgentBody.tsx, globals.css]
tech-stack:
  added: []
  patterns: [client-only useCoAgent inside rail, arbitrary Tailwind breakpoint min-[880px]:]
key-files:
  created:
    - frontend/src/components/SessionRail.tsx
  modified:
    - frontend/src/app/page.tsx
    - frontend/src/components/AgentBody.tsx
    - frontend/src/app/globals.css
decisions:
  - "Used Tailwind arbitrary breakpoint `min-[880px]:` instead of the default `md:` (768px) to match the 880px collapse threshold from design-direction.md exactly."
  - "Kept AgentBody.tsx (stripped to a renders-null component) because the wildcard `useCopilotAction({ name: '*' })` registration still needs a mount point inside <CopilotKit>. Deletion candidacy deferred to 05-04."
  - "Replaced the global `main { display: flex; flex-direction: column; min-height: 100vh }` reset with per-page layout (grid-rows-[48px_1fr] h-screen in page.tsx, max-width column in SessionPicker via its own main element)."
metrics:
  duration: ~25min
  completed: 2026-05-16
---

# Phase 5 Plan 01: UI-P3 Structural Fix Summary

Replace `<CopilotSidebar>` with inline `<CopilotChat>` in a 2-column CSS Grid;
new `SessionRail` houses session card / runner-state pill / artifacts list.
Closes the F-1 "75% black space" complaint at its source.

## Decisions Made

- **Breakpoint:** `min-[880px]:` matches design-direction.md exactly. Tailwind's
  default `md:` (768px) would have been close but not faithful.
- **AgentBody fate:** retained as a renders-null wildcard tool-action
  registration site. Phase 05-04 will decide whether to inline the 4-line hook
  directly into page.tsx.
- **Global `main {}` reset removed:** layout is now per-page; SessionPicker's
  centred column and chat-view grid each declare their own shape.

## Tasks Completed

| Task | Name                                                     | Commit  |
| ---- | -------------------------------------------------------- | ------- |
| —    | Plan markdown                                            | 722f38d |
| 1    | Create SessionRail.tsx with three sections               | 629f28f |
| 2+3  | Grid layout + CopilotChat + AgentBody slim + globals.css | d69dc10 |

## Deviations from Plan

None — plan executed as written; only refinement was choosing the exact
`min-[880px]:` arbitrary breakpoint (called out in plan as Claude's discretion).

## Verification

- `npm run build` — clean (5 routes, no TS errors).
- `grep -n 'CopilotSidebar' frontend/src/` — empty.
- `grep -n 'AgentBody' frontend/src/app/page.tsx` — 2 references (import + mount inside CopilotKit), both required to keep the wildcard tool renderer wired.
- Manual visual check pending in 05-02 (combined with polish review).

## Self-Check: PASSED

- FOUND: frontend/src/components/SessionRail.tsx
- FOUND: commit 722f38d (plan), 629f28f (SessionRail), d69dc10 (grid + AgentBody + globals)
