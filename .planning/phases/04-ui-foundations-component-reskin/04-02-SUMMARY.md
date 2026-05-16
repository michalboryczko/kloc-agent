---
phase: 04
plan: 02
subsystem: frontend
tags: [reskin, tailwind, design-tokens, components, extraction]
requires: [UI-P1]
provides:
  - reskinned-toolcallcard
  - reskinned-agentbody
  - extracted-sessionpicker
  - reskinned-chat-header
affects:
  - frontend/src/components/ToolCallCard.tsx
  - frontend/src/components/AgentBody.tsx
  - frontend/src/components/SessionPicker.tsx
  - frontend/src/app/page.tsx
tech-stack:
  added: []
  patterns:
    - "Token-var inline references inside Tailwind classes: bg-[var(--bg-2)]"
    - "cn() helper for conditional class composition (denial branch on ToolCallCard)"
    - "group + group-open: for animated chevrons on <details>"
    - "group + group-hover: for reveal-on-hover arrow in SessionPicker rows"
key-files:
  created:
    - frontend/src/components/SessionPicker.tsx
  modified:
    - frontend/src/components/ToolCallCard.tsx
    - frontend/src/components/AgentBody.tsx
    - frontend/src/app/page.tsx
decisions:
  - "Kept SessionPicker as a 'use client' component even though it only does prop-rendering. Rationale: the parent page.tsx is already a client component (uses useState), so client-server interop is a non-issue and the picker may grow client-only behaviour (search, filtering) in later phases."
  - "Added aria-busy on the active session row during pickExisting() load. Strictly this belongs to UI-P5 a11y but the cost was zero while we were already rewriting the button — defer formal sign-off to Phase 5."
  - "Used inline arrow span (→) for the hover-reveal indicator instead of a lucide-react icon. Rationale: avoids dragging in lucide-react this phase; Phase 5 can swap to an icon for polish."
metrics:
  tasks: 5
  files-created: 1
  files-modified: 3
  completed: 2026-05-16
---

# Phase 4 Plan 02: UI-P2 Component reskin — Summary

`ToolCallCard`, `AgentBody`, the chat-view header in `page.tsx`, and the
extracted `SessionPicker` are now fully on the editorial-terminal token
theme. Zero inline `style={{}}` blocks remain in the four files this plan
owned; only `Composer.tsx` and `ChatWindow.tsx` still carry them — both are
flagged for deletion under UI-P6 in Phase 5.

## What changed

- **ToolCallCard.** Six inline style blocks → Tailwind + tokens. 2px left
  edge in `--accent` (or `--danger` on denial). Status badge is a
  uppercase-tracked mono pill. Args/result `<pre>` blocks sit on `--bg-0`
  (darker than the card) for code-well depth. Chevron on `<summary>` rotates
  via `group-open:rotate-90`.
- **AgentBody.** Two inline style blocks dropped. Resumed-session hint
  switches from generic blue to the amber accent palette
  (`--accent-soft` / `--accent-line`). Artifacts list adopts mono.
- **SessionPicker extracted.** New file
  `frontend/src/components/SessionPicker.tsx`. Picker UI rebuilt to spec:
  64px serif-italic display title "kloc agent." with the period in `--accent`;
  eyebrow labels in mono uppercase `tracking-[0.18em]`; session rows hover-shift
  3px right with reveal-on-hover arrow; `<Button variant="primary">` for the
  start-new action.
- **Chat-view header reskinned.** Sticky 48px header on `--bg-1` with
  `backdrop-blur`; serif-italic brand mark "kloc *agent*" with the agent word
  in `--accent` and a `[BETA]` mono pill; truncated mono session id on the
  right. Back button uses shadcn ghost variant.

## Commits

| Task | Type | Hash | Message |
|------|------|------|---------|
| Plan | docs | 9c7a8ca1d | docs(04-02): plan UI-P2 component reskin |
| 1 | style | 0d002e1da | reskin ToolCallCard onto token theme |
| 2 | style | ea1accee4 | reskin AgentBody onto token theme |
| 3+4 | feat | 28cda176a | extract SessionPicker + reskin chat-view header |

## Verification (visual smoke check policy)

- `cd frontend && npm run build` → **PASS** (Next 16 Turbopack, 5 routes
  prerendered, no warnings beyond the unrelated `baseline-browser-mapping`
  notice).
- `grep -rn 'style={{' frontend/src/components/{ToolCallCard,AgentBody,SessionPicker}.tsx frontend/src/app/page.tsx`
  → **0 matches** (the four reskinned files).
- `grep -rn 'style={{' frontend/src/{components,app}` → only matches inside
  `Composer.tsx` and `ChatWindow.tsx` — both dead code targeted for deletion
  in Phase 5 UI-P6. Expected and acceptable.
- `grep -c 'function SessionPicker' frontend/src/app/page.tsx` → 0
  (extraction confirmed).
- `grep -c 'from "@/components/SessionPicker"' frontend/src/app/page.tsx` → 1
  (import wired).
- `grep -c 'CopilotSidebar' frontend/src/app/page.tsx` → 2 (import + usage
  preserved; Phase 5 replaces).
- Backend regression sanity: `uv run python -m pytest tests/unit/ -q` →
  **136 passed, 1 skipped** (Postgres-unreachable test) — no change vs
  baseline.

## Deviations from Plan

### Rule 2 — auto-add missing critical functionality

**Added `not-italic` modifier on the `[BETA]` pill in the chat-view header.**
Without it the pill inherits the parent `<h1>`'s `italic` and the mono BETA
text looked slanted. The pill is mono, not serif italic, so the override is
correct.

### Rule 2 — auto-add missing critical functionality

**Added `truncate` to the tool-name `<strong>` in ToolCallCard.** Long tool
names (e.g. `kloc_search_php_symbol_definitions`) would otherwise push the
status badge off the card. Added `gap-3` on the parent flex so they always
keep breathing room.

### Rule 3 — fix blocking issues (auto)

**Plan said "eyebrow uses `▌` or `&mdash;&mdash;&mdash;`".** Settled on a
plain em-dash + space ("— analyst chat") to keep it copy-paste-able and to
avoid a heavier brand mark in the eyebrow row.

## Threat Flags

None. Plan is a pure reskin — no new network surface, no auth paths, no
schema changes.

## Self-Check

- [x] `frontend/src/components/SessionPicker.tsx` exists (verified)
- [x] `frontend/src/components/ToolCallCard.tsx` has 0 inline styles (verified)
- [x] `frontend/src/components/AgentBody.tsx` has 0 inline styles (verified)
- [x] `frontend/src/app/page.tsx` has 0 inline styles (verified)
- [x] `frontend/src/components/SessionPicker.tsx` has 0 inline styles (verified)
- [x] `<CopilotSidebar>` still mounted in page.tsx (verified, count=2)
- [x] All 3 task commits present in git log (verified: 0d002e1da, ea1accee4, 28cda176a)
- [x] `npm run build` succeeds (verified)
- [x] Backend `pytest tests/unit/` still green (136 passed, 1 skipped)

## Self-Check: PASSED
