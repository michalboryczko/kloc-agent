# Implementation plan

Phased so the app keeps working after each phase. Assumes Tailwind v4 was chosen in `styling-architecture.md` — if CSS Modules is chosen instead, the work shifts but the phasing stays the same.

---

## Phase 0 — Decisions (no code)

Output: written answers to the four questions in `styling-architecture.md` § Open questions.

1. Tailwind or CSS Modules?
2. Adopt shadcn/ui?
3. Dark only, or dark + light?
4. Fonts via `next/font` or `@import url(…)`?

**Estimated effort:** 30 minutes of conversation.

---

## Phase 1 — Foundations

Install the styling stack and lock in tokens. No visible UI change yet.

1. **Install Tailwind v4** + PostCSS plugin. Configure `postcss.config.mjs`.
2. **Replace `globals.css`** with:
   - `@import "tailwindcss";`
   - `@theme {…}` block with every token from `design-direction.md`
   - `@layer base` reset (`html, body, *`)
   - Radial gradient atmosphere on `body`
   - Grain overlay on `body::before`
   - CopilotKit CSS variable overrides (cross-cutting concern, lives in globals)
3. **Load fonts via `next/font`** in `layout.tsx`:
   - `Instrument_Serif` (display)
   - `Geist` (body)
   - `JetBrains_Mono` (mono)
   Wire them as CSS variables on `<html>`.
4. **Verify** the existing app still renders. Tokens are defined but not used yet — page should look almost identical, just on the new background colour.

**Estimated effort:** 1–2 hours. Mostly config, no creative work.

**Risk:** Tailwind v4's `@import "tailwindcss"` replaces the entire `@tailwind base/components/utilities` triplet. Some old guides still show the v3 syntax — follow the v4 docs.

---

## Phase 2 — Component reskin (visible changes start here)

Replace inline styles with Tailwind classes. One file at a time, smallest first.

Order:
1. **`ToolCallCard.tsx`** — single component, isolated, lowest risk. Establishes the card pattern (border + 2px accent edge, badge pill, monospace name, animated chevron).
2. **`AgentBody.tsx`** — strip out the inline styles, move the resume-hint into a designed callout.
3. **Session picker (extract `SessionPicker.tsx` from `page.tsx`)** — biggest visible win. Implements the editorial display headline, the amber primary button, the hover-shift session rows.
4. **Chat view header + layout in `page.tsx`** — sticky header with brand mark, switch to two-column grid.

After step 4 the user can re-run the app and the design direction will be visible.

**Estimated effort:** 4–6 hours for an unhurried, polished pass.

---

## Phase 3 — Structural fix: replace Sidebar with inline Chat

**This is the change that fixes the "75% black space" complaint.**

1. Add a new component **`SessionRail.tsx`** containing the rail described in `design-direction.md` (session card, runner-state pill, artifacts list).
2. Replace `<CopilotSidebar>` in `page.tsx` with `<CopilotChat>` (also exported by `@copilotkit/react-ui`).
3. Wrap the chat view body in a grid:
   ```tsx
   <div className="chat-layout">
     <SessionRail … />
     <div className="chat-pane">
       <CopilotChat … />
     </div>
   </div>
   ```
4. Wire the artifacts list from `useCoAgent<KlocAgentState>().state.artifacts` into `<SessionRail>` (move the existing wiring out of `AgentBody`).
5. Override CopilotKit's CSS variables so the chat panel inherits our theme. The variables to set in `globals.css`:
   - `--copilot-kit-primary-color`
   - `--copilot-kit-background-color`
   - `--copilot-kit-input-background-color`
   - `--copilot-kit-separator-color`
   - …etc. See `node_modules/@copilotkit/react-ui/dist/index.d.mts` for the full list.

**Risk:** `<CopilotChat>` has slightly different props from `<CopilotSidebar>`. The `defaultOpen` and `clickOutsideToClose` props don't exist on the inline component — they're sidebar concerns. The `labels` prop is the same. Verify the rendered DOM matches expectations before tearing the sidebar code out.

**Estimated effort:** 2–4 hours including the CopilotKit theme override pass.

---

## Phase 4 — Polish

Things that move the needle from "good" to "memorable".

1. **Page entrance animation** — picker title fades up `220ms`, eyebrow + button + list stagger `20ms` apart. CSS-only via `animation-delay`. Don't animate on the chat view (it's a working surface, not a landing).
2. **Hover shifts on session rows** — already in the design (`translateX(3px)` + arrow appears).
3. **Runner-state dot glow** — single CSS rule, ~3 lines.
4. **Streaming indicator** in the chat input bar (3-dot pulse when waiting for the agent).
5. **Tool card chevron animation** — `transform: rotate(90deg)` on `<details[open]>`.
6. **`<title>` and `<meta>` polish in `layout.tsx`** — description, open-graph image (if we want one). Cheap, looks pro.

**Estimated effort:** 1–2 hours.

---

## Phase 5 — Accessibility / contrast audit

Once the new design lands, sweep through the issues catalogued in `docs/reviews/frontend/accessibility.md`:

- A-1 textarea has no accessible name (now lives in CopilotKit's `<CopilotChat>`, may already be fixed by them — verify)
- A-2 `opacity` low-contrast text — replaced by `--text-mute` / `--text-dim` tokens with measured contrast
- A-3 "← sessions" back button glyph — add `aria-label="Back to sessions"`
- A-4 session row `aria-busy` — add the attribute, restrict `cursor: wait` to the busy row only
- A-5 heading hierarchy — picker keeps `<h1>`, chat view changes to `<h2>` for the session subtitle

**Estimated effort:** 1 hour.

---

## Phase 6 — Delete dead code

Cross-referenced from `docs/reviews/frontend/code-quality.md` CQ-1. Once the new layout is shipped:

- Delete `src/components/ChatWindow.tsx`
- Delete `src/components/Composer.tsx`
- Delete `src/lib/agui-http-agent.ts`
- Delete `src/utils/sseParser.ts`

These four files are self-documented as unused. They are not part of the redesign and shouldn't survive it.

**Estimated effort:** 5 minutes.

---

## Total estimate

- Phase 0: 30 min (conversation)
- Phase 1: 1–2 h
- Phase 2: 4–6 h
- Phase 3: 2–4 h
- Phase 4: 1–2 h
- Phase 5: 1 h
- Phase 6: 5 min

**Range: 10–16 hours of focused work, spread across 2–3 sessions.**

---

## What gets shipped at each checkpoint

| After phase | What the user sees |
|-------------|--------------------|
| 1 | App looks identical, but the theme background is the new dark base. |
| 2 | Session picker and tool cards look like a real product. Chat view still has the floating sidebar void. |
| 3 | **Chat view problem fixed.** Layout reads as a proper app. |
| 4 | Motion and small details bring the polish from "good" to "memorable". |
| 5 | Passes WCAG AA spot-check, screen-reader sanity test. |
| 6 | Repository is clean — no dead modules. |

Phases 1–3 are the minimum to address the user's stated complaints. Phases 4–6 are quality work that should be done but can be deferred without leaving the app in a bad state.
