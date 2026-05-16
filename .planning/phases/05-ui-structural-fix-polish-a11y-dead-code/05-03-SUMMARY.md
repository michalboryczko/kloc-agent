---
phase: 05
plan: 03
subsystem: frontend-ui
tags: [ui, a11y, aria, contrast]
requires: [UI-P3, UI-P4]
provides: [A11yChatTextarea helper, scoped busy cursor, full A-1..A-5 closure]
affects: [page.tsx, SessionPicker.tsx]
tech-stack:
  added: []
  patterns: [MutationObserver-driven aria-label injection for third-party widgets, display:contents wrapper]
key-files:
  created:
    - frontend/src/components/A11yChatTextarea.tsx
  modified:
    - frontend/src/app/page.tsx
    - frontend/src/components/SessionPicker.tsx
decisions:
  - "CopilotChat textarea labelled via DOM-side MutationObserver (not via a custom Input component slot). Reason: a full Input override would force us to re-implement CopilotKit's send / stop / attachments / suggestion / chat-context wiring; the bare DOM patch is ~30 lines and works through CopilotKit upgrades as long as a `<textarea>` element exists under the wrapper."
  - "A-2/A-3/A-5 are no-ops in this plan. A-2: `grep` shows zero opacity-on-text callsites — all remaining `opacity` usage is non-text (Tailwind transition states, disabled, hover-reveal arrow, decorative grain). A-3: aria-label was already set in Phase 4. A-5: page.tsx and SessionPicker both use `<h1>` for brand; chat-view session id uses `<h2>` (changed in 05-01)."
metrics:
  duration: ~15min
  completed: 2026-05-16
---

# Phase 5 Plan 03: UI-P5 Accessibility Audit Summary

A-1..A-5 closed: textarea labelled, busy cursor scoped, opacity-on-text
swept (none found), back-button label present, heading hierarchy verified.

## A-Item Status

| Item | Required                                       | Action               | Status   |
| ---- | ---------------------------------------------- | -------------------- | -------- |
| A-1  | CopilotChat textarea has accessible name       | A11yChatTextarea     | Patched  |
| A-2  | No `opacity` for muted text                    | grep audit           | Verified |
| A-3  | Back glyph has `aria-label="Back to sessions"` | inspect page.tsx:91  | Verified |
| A-4  | Scoped `cursor: wait` + `aria-busy` on rows    | SessionPicker patch  | Patched  |
| A-5  | `<h1>` brand / `<h2>` chat subtitle            | inspect page.tsx     | Verified |

## A-2 Detailed Sweep

`grep -rn 'opacity' frontend/src/{components,app}` results:

| File:Line                            | Use                                          | Verdict |
| ------------------------------------ | -------------------------------------------- | ------- |
| SessionPicker.tsx:109                | `opacity-0 group-hover:opacity-100` arrow    | OK (decorative reveal, not muted text) |
| ui/button.tsx:36                     | `disabled:opacity-50`                        | OK (disabled-state convention) |
| ui/textarea.tsx, ui/input.tsx        | `disabled:opacity-50`                        | OK (same) |
| globals.css:122                      | `opacity: 0.035` on `body::before` grain     | OK (decorative grain overlay) |

No text-muting via opacity remains.

## Tasks Completed

| Task | Name                                              | Commit  |
| ---- | ------------------------------------------------- | ------- |
| —    | Plan markdown                                     | 205ae88 |
| 1    | A-1 — Label CopilotChat textarea                  | 88d0d29 |
| 2    | A-2 — Opacity sweep (verification only)           | (n/a)   |
| 3    | A-3 — Back-button aria-label (verification only)  | (n/a)   |
| 4    | A-4 — Scoped cursor:wait on busy row              | 59f0571 |
| 5    | A-5 — Heading hierarchy (verification only)       | (n/a)   |

## Deviations from Plan

None — plan executed as written; three items were verification-only as
anticipated, two required code patches.

## Self-Check: PASSED

- FOUND: frontend/src/components/A11yChatTextarea.tsx
- FOUND: commits 205ae88, 88d0d29, 59f0571
- FOUND: aria-label="Ask kloc analyst" is set by useEffect MutationObserver in A11yChatTextarea
- FOUND: `aria-label="Back to sessions"` at frontend/src/app/page.tsx (back button)
