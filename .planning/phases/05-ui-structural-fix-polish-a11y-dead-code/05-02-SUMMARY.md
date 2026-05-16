---
phase: 05
plan: 02
subsystem: frontend-ui
tags: [ui, motion, polish, animations, metadata]
requires: [UI-P3 — new layout in place]
provides: [fade-up entrance cascade, pulse-dot keyframe, StreamingDots, polished metadata]
affects: [globals.css, SessionPicker.tsx, layout.tsx]
tech-stack:
  added: []
  patterns: [CSS keyframes in @layer utilities, motion-safe: prefix for reduced-motion compliance]
key-files:
  created:
    - frontend/src/components/StreamingDots.tsx
  modified:
    - frontend/src/app/globals.css
    - frontend/src/components/SessionPicker.tsx
    - frontend/src/app/layout.tsx
decisions:
  - "Used `motion-safe:` Tailwind prefix at each callsite instead of a global `@media (prefers-reduced-motion: reduce)` override — keeps the reduction logic visible per-element and lets future animations opt in or out independently."
  - "Did NOT wire StreamingDots into the CopilotChat surface — CopilotKit ships its own native streaming indicator inside the chat container; mounting a second indicator would compete with it. The keyframe is kept available for non-Copilot surfaces (transcript view, future status strip)."
  - "Tool-card chevron (`group-open:rotate-90`) and runner-state pill glow (`shadow-[0_0_8px_rgba(74,222,128,0.5)]`) were already in place from Phase 4 / 05-01 — no patch needed."
metrics:
  duration: ~15min
  completed: 2026-05-16
---

# Phase 5 Plan 02: UI-P4 Polish Summary

CSS-only animation layer + metadata refresh. Five animation primitives now
defined; entrance cascade applied to the picker; chevron + glow verified
in place from earlier phases.

## Animations Inventory

| Animation                          | Where defined                    | Applied at                         |
| ---------------------------------- | -------------------------------- | ---------------------------------- |
| `kloc-fade-up` (220ms)             | globals.css `@layer utilities`   | SessionPicker headline             |
| `kloc-fade-up-delay-1/2/3`         | globals.css                      | SessionPicker eyebrow / btn / list |
| `kloc-pulse-dot` (1200ms wave)     | globals.css                      | StreamingDots component            |
| Session-row hover translate (160ms) | SessionPicker (Tailwind utility) | Existing from Phase 4              |
| Tool-card chevron rotate (120ms)   | ToolCallCard (Tailwind utility)  | Existing from Phase 4              |
| Pill glow (shadow)                 | SessionRail (Tailwind utility)   | From 05-01                         |

## Tasks Completed

| Task | Name                                              | Commit  |
| ---- | ------------------------------------------------- | ------- |
| —    | Plan markdown                                     | 2f85338 |
| 1–5  | Keyframes + picker apply + dots + metadata        | 4c564e6 |

## Deviations from Plan

**[Rule 1 — Scope refinement]** Task 3 originally proposed wiring StreamingDots
into the chat pane. On inspection CopilotKit already provides its native
streaming indicator in the chat input bar; mounting a second one would
duplicate UX. The component is exported and ready for non-Copilot surfaces
that will surface in later phases (e.g. transcript viewer). This keeps the
keyframe consumed and the deliverable count honest (5+ animations defined,
4 actively applied today).

## Verification

- `npm run build` — clean.
- `grep '@keyframes kloc-' frontend/src/app/globals.css` — 2 matches.
- `grep 'animate-kloc-fade-up' frontend/src/components/SessionPicker.tsx` — 4 matches (headline + 3 delays).
- Visual smoke check pending sign-off in 05-03 / 05-04.

## Self-Check: PASSED

- FOUND: frontend/src/components/StreamingDots.tsx
- FOUND: commit 2f85338 (plan), 4c564e6 (implementation)
- FOUND: keyframes `kloc-fade-up` and `kloc-pulse-dot` in globals.css
