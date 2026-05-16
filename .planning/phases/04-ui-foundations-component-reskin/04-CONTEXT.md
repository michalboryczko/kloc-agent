# Phase 4: UI foundations & component reskin - Context

**Gathered:** 2026-05-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous) — 4 explicit design decisions locked

<domain>
## Phase Boundary

Lock the four open design decisions, install the styling stack, reskin
existing components onto the new token theme. **Do not change the chat-view
structure yet** — that is Phase 5 work (replacing `<CopilotSidebar>` with
inline `<CopilotChat>` + `SessionRail`). App must remain operational at
every commit.

In scope: UI-P0 (4 decisions), UI-P1 (styling stack + tokens), UI-P2 (reskin
of ToolCallCard, AgentBody, SessionPicker, page.tsx header).

Out of scope: replacing CopilotSidebar (Phase 5), accessibility audit
(Phase 5), dead-code deletion (Phase 5), polish micro-interactions
(Phase 5).

</domain>

<decisions>
## Implementation Decisions

### UI-P0 — Four design decisions (LOCKED)

1. **Styling stack:** Tailwind v4 (with `@theme` tokens). Endorsed by
   `docs/reviews/ui-design-review/styling-architecture.md`. The codebase has
   4 components — this is the cheapest moment to adopt. `@theme` is exactly
   a token-based design system in CSS; the design-direction.md tokens map
   onto it 1:1. Component classes via `@layer components`; variants
   (`hover:`, `focus:`, `dark:`) immediately solve the "no hover states"
   complaint.

2. **shadcn/ui:** Adopt. Accessible Radix-based components copy-paste built
   on Tailwind, no runtime dependency. Useful when Dialog/Tooltip/Popover
   land (artifact viewer, settings menu). No new dependency footprint
   beyond what shadcn writes into the codebase.

3. **Theme:** Dark only. The design direction is dark-first; light theme
   doubles design QA cost for zero v1 value. Tokens still use the
   `--bg-0` / `--bg-1` / etc structure so a light theme can be added
   later via a `[data-theme=light]` block without re-architecture.

4. **Font loading:** `next/font`. Subsetted, no FOUT, production-grade.
   Faces declared once in `frontend/src/app/layout.tsx`.

### UI-P1 — Styling stack install

- Install `tailwindcss@^4`, `@tailwindcss/postcss`, `tailwindcss-animate`,
  `clsx`, `tailwind-merge`. (Versions resolved by `npm install` —
  no version pinning beyond major.)
- Replace `frontend/src/app/globals.css` content. Keep file path. New
  content: `@import "tailwindcss";` + `@theme { ... }` with tokens from
  `design-direction.md`.
- Configure PostCSS via `frontend/postcss.config.mjs` per Tailwind v4 docs.
- Set up shadcn/ui: run `npx shadcn@latest init` once (interactive — skip
  the install of unused components). Output: `components.json`,
  `lib/utils.ts` (cn helper), seed Tailwind tokens preserved.
- Load fonts: `Instrument Serif`, `Geist`, `JetBrains Mono` via
  `next/font/google` in `layout.tsx`. Apply CSS variables `--serif`,
  `--sans`, `--mono` via `body` class.

### UI-P2 — Component reskin

- `frontend/src/components/ToolCallCard.tsx` — reskin onto token theme,
  remove inline `style={{}}` blocks, use Tailwind classes for hover/focus
  states.
- `frontend/src/components/AgentBody.tsx` — reskin, same treatment.
- Extract `SessionPicker.tsx` from `frontend/src/app/page.tsx` to
  `frontend/src/components/SessionPicker.tsx`; reskin to the picker title
  + meta look from `design-direction.md` (display serif, monospace
  IDs/timestamps).
- Reskin the chat-view header inline in `page.tsx`. Keep `<CopilotSidebar>`
  for now — Phase 5 replaces it.
- After Phase 4 completes: `grep -rn 'style={{' frontend/src/{components,app}`
  shows only files NOT reskinned in this phase (i.e., not these four).

### Atmosphere background

- `body` gets radial gradient + subtle grain overlay per `design-direction.md`.
  Implementation via Tailwind `bg-[...]` or a single CSS-Module-style block
  in `globals.css` — Claude decides at implementation time.

### CopilotKit theme override

- Set CopilotKit CSS variables in `globals.css` so the chat panel inherits
  `--bg-1`, `--text`, `--accent` etc. The library's defaults are blue; need
  to override to amber per design direction.

### Claude's Discretion
- Exact shadcn/ui components to pre-install (recommend Button, Input,
  Textarea, Card baseline — others on demand).
- Tailwind `tailwind-merge` adoption for the `cn` helper pattern.
- File-by-file commit boundary (one component per commit vs. one
  styling-stack-setup commit + one reskin commit per component).
- Whether to extract a `lib/cn.ts` separately or accept shadcn's
  `lib/utils.ts`.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `docs/reviews/ui-design-review/design-direction.md` — full token system
  (color, type scale, spacing, radius, shadow) ready to map to `@theme`.
- `docs/reviews/ui-design-review/styling-architecture.md` — decision
  rationale; can be referenced from PLAN.md tasks.
- `docs/reviews/ui-design-review/implementation-plan.md` — original phase
  plan; the ROADMAP Phases 4/5 derive from this.

### Established Patterns
- Next.js 16 App Router; `frontend/src/app/layout.tsx` is the font/CSS root.
- Components in `frontend/src/components/`; pages in `frontend/src/app/`.
- TypeScript strict mode; path alias `@/*` → `./src/*`.

### Integration Points
- `frontend/package.json` — add Tailwind v4 + shadcn deps.
- `frontend/postcss.config.mjs` — new file (Tailwind v4 PostCSS plugin).
- `frontend/src/app/globals.css` — replace content entirely.
- `frontend/src/app/layout.tsx` — load fonts, apply body classes.
- `frontend/src/app/page.tsx` — header reskin + SessionPicker extraction.
- `frontend/src/components/{ToolCallCard,AgentBody}.tsx` — reskin.
- `frontend/src/components/SessionPicker.tsx` — new (extracted from page.tsx).
- `frontend/components.json` — shadcn config (generated by `init`).
- `frontend/src/lib/utils.ts` — `cn` helper (generated by shadcn `init`).

</code_context>

<specifics>
## Specific Ideas

- Aesthetic: "Editorial Terminal" — refined dark dev-tool, hairline borders,
  serif italic display + mono badges, warm amber (`#f5a524`) accent. Per
  `design-direction.md`.
- "Bigger" components (e.g. Composer, ChatWindow) are out of Phase 4 scope.
  They get reskinned naturally as part of Phase 5's CopilotSidebar
  replacement.
- App must boot at every commit. No commit that leaves Next.js failing
  `next build` or `next dev`.

</specifics>

<deferred>
## Deferred Ideas

- Light theme support — deferred indefinitely (no v1 need).
- Custom font hosting (self-hosted woff2 instead of `next/font/google`) —
  deferred; `next/font/google` is good enough and avoids font licensing
  questions.
- Storybook / component gallery — out of scope.
- Theme switcher UI — out of scope (no light mode to switch to).

</deferred>
