# UI Design Review — kloc-agent frontend

Review date: **2026-05-16**
Scope: `frontend/src/app/page.tsx`, `frontend/src/components/AgentBody.tsx`, `frontend/src/components/ToolCallCard.tsx`, `frontend/src/app/globals.css`, `frontend/src/app/layout.tsx`

This is a **design / look-and-feel review**, separate from the code-quality review in `docs/reviews/frontend/`. The earlier review covered correctness, performance, a11y, and security at the code level. This review answers a different question: *does the app look like a serious, modern product, or like a stub?*

---

## TL;DR

The current UI looks like an early-prototype unstyled scaffold:

1. **The chat view is ~75% empty black space** because `<CopilotSidebar>` floats in a corner of the page while the main column holds a single small paragraph in `AgentBody`. The page has no real layout — just a header above a void.
2. **Visual identity is generic.** Inline styles, system sans-serif, two hardcoded fallback rgba grays, one blue accent that only appears on a button background. No typography hierarchy, no atmosphere, no character.
3. **Inline styles everywhere** make iteration slow, prevent reuse, prevent theming, and break the moment a designer wants to tweak anything.
4. **No design tokens.** Colors, spacing, radii, and font sizes are hardcoded at every callsite — `padding: "16px 24px"`, `borderRadius: 8`, `rgba(120, 120, 120, 0.2)`, `opacity: 0.7`.

None of this is a bug. The app works. But it reads as "first day dev work" because there is no visual system underneath the components.

---

## Documents in this review

| File | Purpose |
|------|---------|
| [`findings.md`](./findings.md) | Concrete issues with the current UI, with file:line citations |
| [`design-direction.md`](./design-direction.md) | Proposed aesthetic ("Editorial Terminal"), token system, layout sketches |
| [`styling-architecture.md`](./styling-architecture.md) | Comparison of Tailwind / CSS Modules / SCSS modules / vanilla-extract — recommendation and migration shape |
| [`implementation-plan.md`](./implementation-plan.md) | Phased rollout: foundations → layout → polish |

---

## Recommended order of action

1. Read [`styling-architecture.md`](./styling-architecture.md) and **decide on a styling stack** (Tailwind v4 is the recommendation). This is the foundational decision — every other change depends on it.
2. Read [`design-direction.md`](./design-direction.md) and confirm or redirect the aesthetic. Two or three small mockups can be sketched before committing.
3. Execute the plan in [`implementation-plan.md`](./implementation-plan.md) — phased so the app keeps working after each phase.

---

## What this review does NOT cover

- **Brand identity** (logo, marketing site, product naming). The proposal here assumes the name "kloc-agent" stays.
- **Feature additions** (artifacts viewer, code preview pane, multi-pane workspace). The proposal *makes room* for these but does not design them.
- **Mobile / responsive design beyond a single breakpoint** at ~880px. The product is a desktop dev tool; full mobile is out of scope.
- **The CopilotKit chat panel internals** — only the surrounding chrome and the CSS-variable theme overrides CopilotKit exposes.
