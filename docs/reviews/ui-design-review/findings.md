# Findings — current UI

Concrete problems with the current visual design, with file:line citations.

---

## F-1 — Chat view is mostly empty black space *(critical)*

**Files:** `src/app/page.tsx:82-134`, `src/components/AgentBody.tsx:57-95`

The chat view structure today:

```
<main>                        ← flex column, min-height: 100vh
  <header>                    ← slim bar, 16px padding
  <CopilotKit>
    <AgentBody>               ← <section> flex: 1, holds ONLY a hint paragraph
    <CopilotSidebar>          ← absolutely positioned floating panel (right corner)
  </CopilotKit>
</main>
```

`<AgentBody>` is a `<section style={{ flex: 1 }}>` containing one paragraph ("Ask a question about the indexed PHP codebase…") plus, conditionally, a resume hint or a short artifacts `<ul>`. On a 1440×900 viewport that fills ~75% of the screen with empty background while the actual chat lives in the corner widget.

**Why this happens:** `<CopilotSidebar>` is designed to overlay an existing app — it expects the rest of the page to be *something*. Here there is no "rest of page" — just the hint.

**Fix shape (see `design-direction.md` for full):**
- Swap `<CopilotSidebar>` → `<CopilotChat>` (inline, fills its container). Both are exported by `@copilotkit/react-ui`.
- Give the main area a real grid: left rail (session metadata, runner status, artifacts) + center column (chat).

---

## F-2 — No visual identity *(high)*

**Files:** `src/app/globals.css` (entire file is 33 lines), `src/app/layout.tsx`

The current `globals.css` defines:
- 3 CSS variables (`--background`, `--foreground`, `--kloc-accent`)
- A `prefers-color-scheme: dark` block toggling the two BG/FG values
- A system-font stack
- A `main` flex layout reset

That is the entire design system. Consequence:

- **Typography is system default** (`ui-sans-serif, system-ui, …`). No display font, no mono font, no scale, no weights chosen.
- **The accent (`#1c5dff`) is defined but never used.** The single blue button in `page.tsx:168` uses `rgba(80, 130, 220, 0.12)` inline — a different blue altogether.
- **No spacing scale, no radius scale, no shadow scale.** Every value is hand-picked at the call site.
- **No atmosphere.** Pure flat background, no gradients, no texture, no depth cues.

The result reads as "default browser styles + a few inline tweaks", which is what the user described as "first day dev work."

---

## F-3 — Inline styles everywhere *(high)*

**Files:** `src/app/page.tsx` (24 inline `style={{ }}` blocks), `src/components/AgentBody.tsx:58-94`, `src/components/ToolCallCard.tsx:53-110`

Examples:

```tsx
// page.tsx:85
style={{
  padding: "16px 24px",
  borderBottom: "1px solid rgba(120, 120, 120, 0.2)",
  display: "flex",
  alignItems: "center",
  gap: 16,
}}

// page.tsx:164
style={{
  padding: "10px 16px",
  border: "1px solid rgba(120, 120, 120, 0.4)",
  borderRadius: 8,
  background: "rgba(80, 130, 220, 0.12)",
  cursor: busyId !== null ? "wait" : "pointer",
  fontSize: 14,
  fontWeight: 600,
}}
```

Problems:

- **No reuse.** Two buttons need two style objects; a third button means a third copy.
- **Hover/focus/active states cannot be expressed** with `style={{}}` (no pseudo-selectors). The app currently has no hover states at all — buttons are static.
- **No `@media` queries.** Can't go responsive without rewriting components.
- **No theming.** Light/dark would require conditional logic at every callsite.
- **The grays are inconsistent.** `rgba(120, 120, 120, 0.15)`, `0.2`, `0.25`, `0.35`, `0.4` — five different greys for borders. Same shade family, hand-picked each time.

The codebase needs a styling architecture (see `styling-architecture.md`).

---

## F-4 — `opacity` used for low-emphasis text *(medium, also a11y)*

**Files:** `src/app/page.tsx:109,155,185,189,234`, `src/components/AgentBody.tsx:98`, `src/components/ToolCallCard.tsx:72,76,93`

Pattern repeated throughout:

```tsx
<p style={{ opacity: 0.6, fontSize: 13 }}>…</p>
```

`opacity` on text mixes the text colour with whatever is behind it, so the actual rendered contrast depends on the background. At 0.6 against a near-black background you get a mid-grey that often falls below WCAG AA 4.5:1. Worse, the app reacts to `prefers-color-scheme`, so the same `opacity: 0.6` paragraph reads dramatically different in light vs. dark mode.

**Fix:** Define a `--text-mute` and `--text-dim` token with measured contrast ratios in both themes. Reserve `opacity` for non-text visuals (disabled buttons, decorative graphics).

This issue is cross-referenced from `docs/reviews/frontend/accessibility.md` (A-2).

---

## F-5 — No typographic hierarchy *(medium)*

**Files:** `src/app/page.tsx:108,154`, `src/components/AgentBody.tsx:67`

The current size scale, sampled:

| Element | Size | Weight | Family |
|---------|------|--------|--------|
| `<h1>kloc-agent</h1>` (chat header) | `fontSize: 20` inline | default (700) | system sans |
| `<h1>kloc-agent</h1>` (picker) | default `<h1>` (≈ 2em) | default | system sans |
| Session title | `fontSize: 14, fontWeight: 500` | 500 | system sans |
| Body paragraph (hint) | `fontSize: 14` | regular | system sans |
| Session meta | `fontSize: 12` | regular | system sans |
| Tool card name | `fontFamily: ui-monospace` (only here) | bold | mono |
| Session id slice | `fontFamily: monospace` (only here) | regular | mono |

There is no scale (e.g., 12 / 13 / 14 / 16 / 20 / 24 / 32 / 48). Pages have only one font family and a near-flat hierarchy, so nothing reads as "the title" — the brand mark "kloc-agent" sits at the same visual weight as a session row.

**Fix:** Pick a display font (serif italic accent is a strong move for a developer tool — distinctive without being noisy), pair with the existing body sans, and establish a type scale (recommendation in `design-direction.md`).

---

## F-6 — Empty-state and resume-hint blocks are afterthoughts *(low–medium)*

**Files:** `src/components/AgentBody.tsx:67-93`

The chat panel's main column shows, in order:
1. A static instructional sentence (always).
2. *If resumed*, a callout: "Resumed session — {n} prior messages will appear in the chat once you send your next message."
3. *If artifacts*, a bare `<ul>` of filenames.

These are useful information, but they sit in the middle of the empty void described in F-1, looking like leftover scaffolding text. The resume hint in particular uses the only non-grey accent in the app (`rgba(80, 130, 220, 0.08)` / `0.25`), but only when the user resumed a session — it shows up rarely and looks unrelated to the rest of the UI.

**Fix:** Move artifacts and runner-state out of the main area into a left rail (which gives them a permanent home), and treat the empty state as a designed thing — a hero card with a clear "what to do next" prompt rather than just italic text.

---

## F-7 — Session picker is a wall of plain text *(medium)*

**Files:** `src/app/page.tsx:145-247`

The landing screen is functionally fine — title, "Start new chat" button, list of sessions — but visually:
- The "kloc-agent" `<h1>` uses the default browser size and weight, no character.
- The "+ Start new chat" button has a soft blue background but no clear primary-action treatment (no shadow, no contrast, no hover state).
- Session rows are dividing-line-only — no hover affordance, no visual cue that they are clickable, no animation when you press one.
- The error message uses `color: "crimson"` (named CSS color), inconsistent with the rest of the rgba palette.

This is the first page a user sees. It should establish the design language; today it does the opposite.

---

## F-8 — `<CopilotSidebar>` styling is unthemed *(low–medium)*

**File:** `src/app/layout.tsx:2` (imports `@copilotkit/react-ui/styles.css`)

CopilotKit ships its own CSS and exposes theme hooks via CSS variables (`--copilot-kit-primary-color`, `--copilot-kit-background-color`, etc., per the v1.56 docs). Currently we import their stylesheet and don't override any of these — so the chat panel uses CopilotKit's defaults (which are tuned for a light page) regardless of what the rest of the app does.

**Fix:** Override the CopilotKit CSS variables in our globals (or via Tailwind layers) so the chat panel inherits the same accent, background, and typography as everything else.

---

## F-9 — No loading / streaming visuals *(low)*

**Files:** `src/app/page.tsx:228-232` (the only loading indicator)

When picking a session, the row shows the text "loading…" in muted grey. When CopilotKit streams a response, it does its own thing (we don't surround it with skeletons or progress bars). When MCP tools run, the `ToolCallCard` switches its badge text but has no animation.

A modern dev tool gets visual feedback right: pulse on the active streaming row, a thin progress bar at the top of the chat, a spinning glyph next to "executing" tool calls. None of this exists today.

**Fix:** Add a small set of animation primitives (a 6×6 dot pulse, a top progress bar, a subtle skeleton shimmer). Tailwind makes this nearly free (`animate-pulse` etc.).

---

## F-10 — `next.config.ts` re-exports a `NEXT_PUBLIC_*` var redundantly *(trivial)*

**File:** `frontend/next.config.ts:6-8`

```ts
env: {
  NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000",
},
```

`NEXT_PUBLIC_*` is already inlined into the client bundle by Next.js. The `env` block is redundant and slightly misleading — it suggests the value is being controlled here when in fact Next.js handled it before this ran. Drop the block. Cross-referenced from `docs/reviews/frontend/code-quality.md` (style nits) — repeated here because it intersects with design tokens (see `design-direction.md` § Design tokens / config).
