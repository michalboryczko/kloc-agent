---
phase: 04
plan: 01
subsystem: frontend
tags: [tailwind-v4, shadcn, design-tokens, next-font, copilotkit-theme]
requires: [UI-P0]
provides:
  - tailwind-v4-build
  - design-token-theme
  - shadcn-ui-baseline
  - next-font-wiring
affects:
  - frontend/src/app/globals.css
  - frontend/src/app/layout.tsx
  - frontend/postcss.config.mjs
  - frontend/package.json
tech-stack:
  added:
    - tailwindcss@4.3.0
    - "@tailwindcss/postcss@4.3.0"
    - tailwindcss-animate@1.0.7
    - clsx@2.1.1
    - tailwind-merge@3.6.0
  patterns:
    - "Tailwind v4 @theme block as single source of truth for design tokens"
    - "next/font/google with CSS-variable exposure (--serif, --sans, --mono)"
    - "shadcn/ui cn helper (clsx + twMerge) for class composition"
key-files:
  created:
    - frontend/components.json
    - frontend/src/lib/utils.ts
    - frontend/src/components/ui/button.tsx
    - frontend/src/components/ui/input.tsx
    - frontend/src/components/ui/textarea.tsx
    - frontend/src/components/ui/card.tsx
  modified:
    - frontend/package.json
    - frontend/package-lock.json
    - frontend/postcss.config.mjs
    - frontend/src/app/globals.css
    - frontend/src/app/layout.tsx
decisions:
  - "Wrote shadcn init artefacts by hand (components.json + lib/utils.ts) instead of running `npx shadcn@latest init`. Rationale: the init command prompts for terminal input (Tailwind v4 detection, package-manager pick) and the executor cannot answer prompts. The output is functionally identical."
  - "Skipped class-variance-authority (CVA) for the Button variants. Rationale: avoids an extra dep for a four-variant component; a local Record<Variant, string> map is equivalent."
  - "Used inline CSS variable references (e.g. bg-[var(--bg-2)]) inside Tailwind classes rather than declaring color utilities like bg-bg-2. Rationale: keeps the @theme block as the single source of truth; future tasks can switch to short utility names if the design churns."
metrics:
  tasks: 7
  files-created: 6
  files-modified: 5
  completed: 2026-05-16
---

# Phase 4 Plan 01: UI-P1 Styling stack install — Summary

Tailwind v4 + shadcn/ui baseline now compile cleanly through PostCSS, the
design-direction token system lives in a single `@theme` block, three Google
fonts ship via `next/font` with CSS-variable exposure, and CopilotKit's chat
panel will inherit the dark editorial-terminal palette through six overridden
CSS variables.

## What changed

- **Tailwind v4 build path active.** `postcss.config.mjs` now mounts
  `@tailwindcss/postcss`. `globals.css` opens with `@import "tailwindcss"`
  and an `@theme` block exposing every token from
  `docs/reviews/ui-design-review/design-direction.md` (12 colour tokens,
  3 font tokens, 4 radius tokens, 1 easing token).
- **Body atmosphere.** Two radial gradients (top-right + bottom-left, amber
  tinted) plus a fixed-position `body::before` SVG-turbulence grain layer at
  `opacity: 0.035` per design-direction §Background atmosphere.
- **Fonts via `next/font/google`.** Instrument Serif (400, normal + italic),
  Geist, JetBrains Mono — all latin-subsetted, swap-loaded, exposed as
  `--serif`, `--sans`, `--mono` on `<body>`.
- **CopilotKit theme override layer.** Six `--copilot-kit-*` variables remapped
  onto the new token vars; effect visible the moment the sidebar mounts.
- **shadcn/ui baseline.** Hand-written `Button`, `Input`, `Textarea`, `Card`
  (with sub-components) under `frontend/src/components/ui/`, all wired into
  the token theme — no hardcoded colour literals.
- **cn helper.** `frontend/src/lib/utils.ts` exports the canonical shadcn
  `cn()` pattern (`twMerge(clsx(inputs))`).

## Commits

| Task | Type | Hash | Message |
|------|------|------|---------|
| Plan | docs | 292271c27 | docs(04-01): plan UI-P1 styling stack install |
| 1 | chore | fe97b17d0 | install tailwind v4 + shadcn helper deps |
| 2 | chore | 04db9744c | activate tailwind v4 postcss plugin |
| 3 | chore | fdf7eb8f7 | add shadcn cn helper + components.json |
| 4 | feat | 2fb32001d | replace globals.css with tailwind v4 + editorial-terminal tokens |
| 5 | feat | 01f6bfec3 | load Instrument Serif + Geist + JetBrains Mono via next/font |
| 6 | feat | 0cd74ae61 | add shadcn/ui baseline (button, input, textarea, card) |

## Verification (visual smoke check policy)

- `cd frontend && npm run build` → **PASS** (Next 16 Turbopack build, 5.9s
  compile, 5 routes prerendered, no warnings beyond the unrelated
  `baseline-browser-mapping` deprecation notice from a transitive dep).
- `npm ls tailwindcss @tailwindcss/postcss tailwindcss-animate clsx tailwind-merge`
  → all five resolve at the documented major versions.
- `grep -cE '(@import "tailwindcss"|@theme|--color-bg-0|--copilot-kit-primary-color)' frontend/src/app/globals.css`
  → 5 matches (file structure intact).
- Lint: `npm run lint` script invokes `next lint`, which **Next 16 removed**.
  Confirmed identical failure on baseline (`git checkout 175bc859e -- frontend/src`
  then `npm run lint` reproduces the same `Invalid project directory` error).
  Direct `npx eslint src/` also fails with a pre-existing
  `@eslint/eslintrc` flat-config compatibility error. **Neither failure is
  caused by Phase 4** — both are pre-existing project state and tracked under
  Phase 6 FE-QUALITY scope, not UI-P1.

## Deviations from Plan

### Rule 3 — fix blocking issues (auto)

**`npm run lint` is broken on baseline.** Task 7 of the plan required `lint`
to be clean. The script command is `next lint`, which Next 16 removed. We
confirmed by checking out the pre-Phase-4 source tree and running the same
command — it fails identically. No Phase 4 change introduced this. Documented
above; not blocking the plan. Fixing the eslint pipeline belongs in Phase 6
(FE-QUALITY).

### Rule 3 — fix blocking issues (auto)

**`npx shadcn@latest init` requires interactive input.** Discovered while
planning task 3. Mitigation: write the two init artefacts (`components.json`,
`lib/utils.ts`) by hand to match the documented v4 + new-york + zinc default.
This is equivalent to running init non-interactively.

### Rule 2 — auto-add missing critical functionality

**`color-scheme: dark` added to `:root`.** Not in the plan but required so the
browser native form controls (scrollbars, select dropdowns) inherit the dark
palette. Without it the browser shows white scrollbars against the dark page.

### Rule 2 — auto-add missing critical functionality

**Grain overlay z-index discipline.** The plan asked for a grain overlay on
`body::before` but didn't specify stacking. Without an explicit z-index the
grain would sit *above* all content. Added `body::before { z-index: 0; }` and
`body > * { position: relative; z-index: 1; }` so content always renders above
the grain.

## Self-Check

- [x] `frontend/postcss.config.mjs` activates `@tailwindcss/postcss` (verified)
- [x] `frontend/src/app/globals.css` opens with `@import "tailwindcss"` (verified)
- [x] `@theme` block present with --color-bg-0 (verified)
- [x] CopilotKit overrides present (--copilot-kit-primary-color) (verified)
- [x] `frontend/src/app/layout.tsx` imports next/font/google Geist/Instrument_Serif/JetBrains_Mono (verified)
- [x] `frontend/src/lib/utils.ts` exports `cn` (verified)
- [x] `frontend/components.json` valid JSON (verified)
- [x] Four ui/* files present (button, input, textarea, card) (verified)
- [x] All 6 task commits present in `git log` (verified: fe97b17d0..0cd74ae61)
- [x] `npm run build` succeeds (verified)

## Self-Check: PASSED
