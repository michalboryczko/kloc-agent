# Styling architecture

The user explicitly flagged that **one big `globals.css` is the wrong direction** and asked about SASS or other "compilable" stack. This doc compares the realistic options and recommends one.

---

## TL;DR

**Recommended: Tailwind CSS v4.** Already the dominant Next.js choice, compiles to a single optimised stylesheet at build time, scales from utility classes for one-offs to full component classes via `@apply`, integrates the design tokens from `design-direction.md` as theme values, and has first-class TypeScript IDE support.

Second choice if Tailwind is unwanted: **CSS Modules with PostCSS + design-token CSS variables.** Built into Next.js with zero extra config. Files stay co-located with components, scope is automatic, no runtime cost.

**Not recommended: SCSS modules.** Works fine technically but offers little over CSS Modules now that native CSS has nested selectors, variables, and `@layer`. SCSS's killer features (variables, nesting, mixins) are all in plain CSS today. Recommend skipping SCSS unless there is an existing SCSS investment to migrate.

**Not recommended for this app: vanilla-extract or styled-components.** Vanilla-extract is great but adds significant build / mental overhead for a small surface. Styled-components has React Server Component compatibility issues in Next.js 16 and is in maintenance mode.

---

## Options compared

### Option A — Tailwind CSS v4 *(recommended)*

**What:** Utility-first CSS framework. Write classes inline in JSX (`<button className="bg-amber-500 text-zinc-950 px-3 h-8 rounded-md">`). Tailwind scans the source at build time, generates only the CSS that's used, and emits a single small stylesheet.

**Why for this codebase:**
- The frontend has **4 components**. The migration cost is hours, not days.
- Inline styles already exist everywhere — Tailwind classes are *less verbose* and *more powerful* (hover/focus/dark/responsive variants) than the inline objects they replace.
- Design tokens from `design-direction.md` map directly onto Tailwind theme tokens via the new v4 `@theme` directive — one source of truth.
- For repeated patterns (buttons, cards), `@apply` collapses utilities into component classes (`.btn-primary { @apply bg-amber-500 text-zinc-950 …; }`) in a single `globals.css`.
- Tailwind v4 has a Vite-native engine, builds in <100ms, no `tailwind.config.js` required for tokens (everything lives in CSS via `@theme`).

**Setup shape** (Next.js 16):
```bash
npm install -D tailwindcss @tailwindcss/postcss postcss
```
Add to `postcss.config.mjs`:
```js
export default { plugins: { "@tailwindcss/postcss": {} } };
```
`globals.css` becomes:
```css
@import "tailwindcss";

@theme {
  --color-bg-0: #09090b;
  --color-accent: #f5a524;
  --font-serif: "Instrument Serif", Georgia, serif;
  /* … tokens from design-direction.md … */
}

/* Component classes — only when a utility soup gets noisy */
@layer components {
  .btn-primary {
    @apply h-8 px-3 rounded-md bg-amber-500 text-zinc-950 font-medium
           hover:bg-amber-400 active:translate-y-px transition;
  }
}
```

**Downsides:**
- "Why is my JSX 200 chars wide" — addressed with `clsx` / `cva` for variant logic and with `@apply` for repeated patterns.
- Looks like another buzzword to engineers who haven't used it. Worth noting it has become the industry default for new Next.js projects (Vercel templates, shadcn/ui, the Next.js docs all use it).

### Option B — CSS Modules with design-token CSS variables *(second choice)*

**What:** `Component.module.css` next to `Component.tsx`. Classes are auto-scoped (`.button` becomes `Button_button__a1b2c`). Next.js supports this natively. Design tokens live in `globals.css` as `:root` CSS variables.

**Why for this codebase:**
- Zero new dependencies. Already supported.
- File-co-location keeps related styles close to the component (which is the user's actual concern — they want files broken up, not one giant CSS).
- All the design-direction tokens still live in one `globals.css` `:root` block, but consumption is scoped per component.

**Setup shape:**
```
src/
├── app/
│   ├── globals.css           ← tokens only (~80 lines)
│   ├── page.tsx
│   └── page.module.css       ← layout for the page
├── components/
│   ├── AgentBody.tsx
│   ├── AgentBody.module.css
│   ├── ToolCallCard.tsx
│   └── ToolCallCard.module.css
└── styles/
    └── reset.css             ← optional, imported by globals
```

`page.module.css`:
```css
.chatLayout {
  flex: 1;
  display: grid;
  grid-template-columns: 288px 1fr;
}

.btnPrimary {
  background: linear-gradient(180deg, var(--accent-bright), var(--accent));
  color: #1a1208;
  font-weight: 600;
  /* … */
}

.btnPrimary:hover {
  background: linear-gradient(180deg, #ffc658, var(--accent-bright));
}
```

`page.tsx`:
```tsx
import styles from "./page.module.css";

<button className={styles.btnPrimary}>+ Start new chat</button>
```

**Downsides:**
- More files. Every component gets a sibling `.module.css`.
- Variants get verbose (`clsx({ [s.btnPrimary]: variant === 'primary', [s.btnGhost]: variant === 'ghost' })`).
- No utility shortcuts — every spacing tweak is a CSS write.
- Responsive design via `@media` blocks, not variant prefixes — slightly more verbose.

### Option C — SCSS Modules *(not recommended)*

**What:** Like CSS Modules but with the SCSS preprocessor. `Component.module.scss`.

**Why someone would pick it:** Nesting, mixins, variables, `@use`. Useful in 2018.

**Why not in 2026:**
- Native CSS now has nesting (`& .child { … }`), variables (`var(--token)`), and `@layer` for cascade control. SCSS variables (`$primary`) are *worse* than CSS variables because they don't support runtime theming.
- SCSS adds a build dependency (`sass`) and an extra compile step.
- The only SCSS-only feature that has no native equivalent is mixins, and for this codebase the design system has 1–2 patterns that would benefit, so a couple of utility classes do the job.

**Verdict:** Skip unless there's a hard requirement. CSS Modules + native CSS nesting is strictly simpler and gives 95% of the value.

### Option D — vanilla-extract *(skip)*

**What:** Type-safe CSS-in-TS. Styles written in `.css.ts` files, compiled to static CSS at build time. Zero runtime.

**Why someone would pick it:** Strong typing for tokens, themes-as-types, very ergonomic for large design systems.

**Why not here:**
- The styling surface is small (4 components, ~250 lines of inline style total). The investment-to-payoff ratio doesn't justify it.
- Onboarding cost. Engineers will need to learn it.
- Hard to share with non-engineer collaborators (a designer can read CSS Modules; vanilla-extract is TypeScript).

Revisit if the design system grows past ~20 components or if a design tooling team forms.

### Option E — Styled-components / Emotion *(skip)*

**What:** CSS-in-JS runtime libraries. Tagged-template syntax: `const Btn = styled.button\`...\``.

**Why not:**
- React Server Components hostility. Next.js 16 App Router uses RSC by default. Styled-components needs every styled-using component to be a Client Component, and there's a non-trivial SSR setup ritual.
- Runtime cost (small but non-zero).
- Styled-components is in maintenance mode (no new features since v6).

---

## Decision criteria summary

| Criterion | Tailwind v4 | CSS Modules | SCSS Modules | vanilla-extract | styled-components |
|---|---|---|---|---|---|
| Built into Next.js 16 | Plugin (1 dep) | ✅ native | Plugin (1 dep) | Plugin | Hard with RSC |
| Build cost | Negligible (lightning-css under the hood) | Zero | Small | Small | Runtime |
| Co-located styles | ✅ (inline class) | ✅ (sibling file) | ✅ (sibling file) | ✅ (sibling file) | ✅ (in JSX) |
| Type safety on tokens | Partial (autocomplete) | None | None | ✅ Full | ✅ Full |
| Theme variants | ✅ `dark:` prefix + `@theme` | Manual via `[data-theme]` | Manual | ✅ First-class | ✅ Theming context |
| Responsive variants | ✅ `md:` prefixes | Manual `@media` | Manual `@media` | Manual | Manual |
| Migration friction from inline styles | Lowest (1:1 mapping) | Moderate | Moderate | Highest | Moderate |
| Onboarding cost | Low (industry default) | Lowest | Low | High | Medium |
| Designer-readable | Moderate | ✅ High | ✅ High | Low | Low |

---

## Recommendation

**Adopt Tailwind v4.** Reasoning:

1. The codebase has 4 components and is at an inflection point — the cheapest moment to make this change.
2. The user's stated complaint is "one big CSS file is wrong" — Tailwind eliminates the file entirely except for `@theme` tokens (~80 lines) and a handful of component classes.
3. Tailwind v4 with `@theme` is *exactly* a token-based design system in CSS — the design-direction.md tokens map onto it 1:1.
4. The team can fall back to handwritten CSS at any point via `@layer components` — Tailwind doesn't lock anything out.
5. Variant API (`hover:`, `focus:`, `md:`, `dark:`) immediately solves a real issue: the current inline styles have no hover states.

**If Tailwind is rejected for taste / philosophy reasons:** CSS Modules with CSS-variable tokens. Same design tokens, scoped per component, native to Next.js, no new dependency. This is the most "boring" choice and that's a virtue.

**Either way, drop:** the single `globals.css`-with-everything approach. The file should hold *only* tokens + a small reset + (if Tailwind) the `@theme` directive + (optionally) a few `@layer components` classes.

---

## File layout under each option

### Under Tailwind v4

```
src/
├── app/
│   ├── globals.css           ← @import "tailwindcss"; @theme {…tokens…}; @layer components {…}
│   ├── layout.tsx            ← imports globals.css, font links
│   └── page.tsx              ← className="…" utilities
├── components/
│   ├── AgentBody.tsx
│   ├── ToolCallCard.tsx
│   ├── SessionPicker.tsx     ← extracted from page.tsx
│   └── SessionRail.tsx       ← new, left rail
└── lib/
    └── cn.ts                 ← clsx + tailwind-merge helper
```

### Under CSS Modules

```
src/
├── app/
│   ├── globals.css           ← :root tokens + reset + @import fonts
│   ├── layout.tsx
│   ├── page.tsx
│   └── page.module.css
├── components/
│   ├── AgentBody.tsx
│   ├── AgentBody.module.css
│   ├── ToolCallCard.tsx
│   ├── ToolCallCard.module.css
│   ├── SessionPicker.tsx
│   ├── SessionPicker.module.css
│   ├── SessionRail.tsx
│   └── SessionRail.module.css
└── styles/
    ├── tokens.css            ← could split tokens out of globals
    └── reset.css
```

---

## Open questions for the user

1. **Tailwind or CSS Modules?** This decision unblocks everything else.
2. **Component library?** If Tailwind is picked, do we also want to adopt `shadcn/ui` (copy-paste Radix-based components built on Tailwind)? It would give us accessible primitives (Dialog, Tooltip, Popover) for free — useful when the artifact viewer or settings menu lands.
3. **Dark only, or dark + light?** The proposed direction is dark-first. Light theme is easy under either stack (Tailwind: `dark:` variant; CSS Modules: `[data-theme=dark]` token block) but doubles design QA time.
4. **Fonts via `next/font` or via `@import url(…)`?** `next/font` is faster (subsetted, no FOUT) but requires importing each face in `layout.tsx`. Recommend `next/font` for production.
