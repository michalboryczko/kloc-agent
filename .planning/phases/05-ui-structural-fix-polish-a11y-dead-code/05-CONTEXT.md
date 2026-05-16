# Phase 5: UI structural fix, polish, accessibility, dead code - Context

**Gathered:** 2026-05-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous) — design direction already locked in
docs/reviews/ui-design-review/design-direction.md

<domain>
## Phase Boundary

Fix the "75% black space" complaint (F-1) by replacing `<CopilotSidebar>`
with inline `<CopilotChat>` + a new `SessionRail`. Layer in polish
animations, accessibility audit, and dead-code deletion.

In scope: UI-P3 (structural fix), UI-P4 (polish), UI-P5 (a11y), UI-P6 (dead
code).

Out of scope: anything beyond the items listed in the 4 success criteria.
Frontend code quality (Phase 6) is the next phase.

</domain>

<decisions>
## Implementation Decisions

### UI-P3 — Structural fix (SessionRail + inline CopilotChat)

- **Layout primitive:** CSS Grid (`grid-template-columns: 280px 1fr`). At
  viewport widths below 880px the rail collapses (`display: none`) per
  design-direction.md. Implementation via Tailwind:
  `grid-cols-1 md:grid-cols-[280px_1fr]` (rounded `md:` breakpoint).
- **SessionRail location:** left side, fixed 280px width.
- **Rail sections:** `SESSION` (card with 2px amber left edge — current
  session metadata), `RUNNER STATE` (pill — alive/idle/dead with dot
  glow), `ARTIFACTS` (scrollable list — moved from `AgentBody`).
- **Sticky header:** 48px tall, full-width across both columns.
- **Replace `<CopilotSidebar>` with `<CopilotChat>`** in `page.tsx`. The
  chat fills the right column.

### UI-P4 — Polish layer

- **Animation library:** CSS-only via Tailwind `transition-*` utilities and
  `@keyframes` in `globals.css`. Framer Motion is overkill for sub-300ms
  fades. Tailwind already has `transition-all`, `duration-200`, `ease-out`.
- **Picker entrance:** 220ms title fade-up; staggered 20ms cascade for
  eyebrow → button → list. Implementation: Tailwind animations defined in
  `globals.css` under `@layer utilities` with `animation-delay-*`.
- **Hover effects:** session rows: `translateX(3px)` + arrow reveal on
  hover. `transition-transform duration-150 ease-out`.
- **Runner-state pill glow:** subtle `box-shadow: 0 0 8px rgba(...)` on
  active state (already in design-direction.md tokens).
- **Streaming indicator:** 3-dot pulse in chat input row when agent is
  streaming. `@keyframes pulse-dot { 0%, 80%, 100% { opacity: 0.3 } 40%
  { opacity: 1 } }`.
- **Tool-card chevron:** rotate 90° on `<details[open]>` via
  `[&[open]>summary>svg]:rotate-90 transition-transform`.
- **`<title>` + `<meta>`:** update `layout.tsx` to set page title to
  "kloc agent" + a tagline meta description.

### UI-P5 — Accessibility audit

- **A-1 textarea name:** First verify `<CopilotChat>`'s internal textarea
  has `aria-label` or visible label. If missing, patch via the
  `inputProps` or wrap with a `<label>` element. Don't preemptively wrap.
- **A-2 contrast tokens:** Replace any remaining `opacity: 0.6`-style
  muted text with `--text-mute` / `--text-dim` tokens. Already mostly
  done in Phase 4 reskin.
- **A-3 back-glyph label:** Add `aria-label="Back to sessions"` to the
  back button/arrow in the chat header.
- **A-4 session row busy state:** Add `aria-busy="true"` + scoped
  `cursor: wait` on session rows while their spawn is in flight.
- **A-5 heading hierarchy:** Picker page uses `<h1>` for the brand mark
  ("kloc agent"); chat-view session subtitle uses `<h2>`.

### UI-P6 — Dead code deletion

Delete after Phase 5 UI-P3 lands (so the replacements are live first):
- `frontend/src/components/ChatWindow.tsx`
- `frontend/src/components/Composer.tsx`
- `frontend/src/lib/agui-http-agent.ts`
- `frontend/src/utils/sseParser.ts`

Verify no imports remain: `grep -rn "ChatWindow\|Composer\|agui-http-agent\|sseParser" frontend/src/` returns empty (or only in the files being deleted).

### Plan structure (LOCKED)
- 4 plans, one per requirement: 05-01 (UI-P3), 05-02 (UI-P4), 05-03 (UI-P5),
  05-04 (UI-P6). Sequential: UI-P3 must complete before UI-P4 (polish
  builds on the new layout), UI-P4/UI-P5 before UI-P6 (delete dead code
  last to avoid broken imports during transition).

### Claude's Discretion
- Exact CSS animation property syntax (Tailwind utilities vs. inline
  `@keyframes`).
- Whether `SessionRail` is a single component or split (`SessionCard`,
  `RunnerStatePill`, `ArtifactsList` as sub-components) — Claude decides
  based on file growth.
- Whether to wrap the chevron rotation in a small helper or apply
  Tailwind classes directly.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `frontend/src/components/AgentBody.tsx` — currently houses the artifacts
  list. Phase 5 moves that wiring to `SessionRail`.
- `frontend/src/components/SessionPicker.tsx` — exists from Phase 4.
  Already styled per the design direction.
- `frontend/src/components/ui/` — shadcn baseline (button, input, textarea,
  card). Use for SessionRail's card and runner-state pill.
- `frontend/src/lib/utils.ts` — `cn` helper from Phase 4.
- `frontend/src/app/globals.css` — token theme + body atmosphere from
  Phase 4. Add `@keyframes` + a few `@layer utilities` here for animations.

### Established Patterns
- Tailwind v4 with `@theme` tokens. New animations go in `@layer utilities`.
- shadcn/ui components compose Tailwind classes; consistent with the
  Phase 4 baseline.
- Path alias `@/*` → `./src/*`.

### Integration Points
- `frontend/src/app/page.tsx` — replace `<CopilotSidebar>` with grid +
  `<SessionRail>` + `<CopilotChat>`.
- `frontend/src/components/SessionRail.tsx` — new.
- `frontend/src/app/globals.css` — append animation keyframes.
- `frontend/src/app/layout.tsx` — update `<title>` + `<meta>`.
- `frontend/src/components/ToolCallCard.tsx` — chevron rotation tweak.
- `frontend/src/components/AgentBody.tsx` — artifacts wiring moves out;
  this file may shrink significantly or be eliminated outright.
- Dead modules listed above for deletion.

</code_context>

<specifics>
## Specific Ideas

- The "75% black space" complaint is F-1 — the canonical user pain. The
  visual smoke test for this phase is: open the chat view on a 1440×900
  viewport and confirm the chat fills the visible area (no dark empty
  region around it).
- CopilotChat's behavior with custom layouts may need investigation —
  it's used to being a sidebar. Read its props/CSS surface carefully.
- The 280px rail width is from design-direction.md; the 880px breakpoint
  for collapse is from there too. Don't invent other values.
- Dead-code deletion (UI-P6) overlaps with FE-QUALITY (Phase 6) — verify
  no Phase-6 plan re-references the deleted files.

</specifics>

<deferred>
## Deferred Ideas

- Mobile-optimized chat view — explicitly out of scope; dev tool, mobile is
  a graceful fallback.
- Custom CSS keyframes library beyond what design-direction.md specifies —
  out of scope.
- Switching between sessions without page reload (SPA navigation between
  picker and chat) — already works via existing Next.js routing; no change
  needed.
- Animated transitions between picker → chat view (page transition) —
  nice-to-have, deferred.

</deferred>
