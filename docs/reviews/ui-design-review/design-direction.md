# Design direction — "Editorial Terminal"

A proposed aesthetic for kloc-agent. Concrete enough to implement, opinionated enough to make decisions. Open to redirection — the system below can be retuned to any of several adjacent directions (see *§ Alternatives*).

---

## Aesthetic in one paragraph

A **refined dark dev-tool**. The kind of interface that signals to engineers "this was built with taste" without being flashy. Think Linear, Vercel, Raycast — but with a small editorial twist that keeps it from looking like every other AI-startup product. The twist here: a serif italic display font for the brand mark and headings, contrasted with a precise mono for IDs / tool names / metadata, and a warm amber accent (`#f5a524`) instead of the cliché blue or purple. Hairline borders, subtle radial atmosphere, no decorative graphics — the typography and accent carry the personality.

---

## Tokens

The numbers below are the proposed design system. Use them as CSS variables (or Tailwind theme tokens once the styling stack lands — see `styling-architecture.md`).

### Colour

| Token | Value | Use |
|-------|-------|-----|
| `--bg-0` | `#09090b` | Page background (deepest layer) |
| `--bg-1` | `#0d0d10` | Rail / chat container surface |
| `--bg-2` | `#131318` | Cards, input fields |
| `--bg-elev` | `#1a1a20` | Hover state, focused input |
| `--bg-hover` | `#1f1f26` | Button hover |
| `--line` | `rgba(255,255,255,0.06)` | Hairlines |
| `--line-strong` | `rgba(255,255,255,0.12)` | Visible borders |
| `--line-bright` | `rgba(255,255,255,0.22)` | Hover borders |
| `--text` | `#ededf0` | Primary text |
| `--text-mute` | `#9b9ba4` | Secondary text |
| `--text-dim` | `#5d5d66` | Tertiary / metadata |
| `--accent` | `#f5a524` | Primary action, focus ring, active accents |
| `--accent-bright` | `#ffba3d` | Accent hover / gradients |
| `--accent-soft` | `rgba(245,165,36,0.10)` | Accent background tints |
| `--accent-line` | `rgba(245,165,36,0.35)` | Accent borders |
| `--danger` | `#f87171` | Errors, tool denial |
| `--danger-soft` | `rgba(248,113,113,0.10)` | Error backgrounds |
| `--success` | `#4ade80` | Healthy runner state |

Verified contrast against `--bg-0`:
- `--text` (#ededf0): ≥ 15:1 (AAA)
- `--text-mute` (#9b9ba4): ≥ 7:1 (AAA)
- `--text-dim` (#5d5d66): ≥ 3.5:1 (AA Large only — never use for body text)

### Typography

| Token | Family | Use |
|-------|--------|-----|
| `--serif` | `Instrument Serif`, fallback `Georgia` | Brand mark, picker headline, occasional italic accent |
| `--sans` | `Geist`, fallback system sans | All body / UI text |
| `--mono` | `JetBrains Mono`, fallback `ui-monospace` | Session IDs, tool names, badges, code blocks |

Type scale (steps):
- `text-xs` 10.5px / 11px — uppercase tracked labels (eyebrows)
- `text-sm` 12.5px — meta, badges
- `text-base` 14px — body, chat messages
- `text-md` 15px — picker sub
- `text-lg` 20px — secondary headings
- `text-2xl` 26px — brand mark in header
- `text-display` 64px — picker title (`--serif` italic)

Letter-spacing:
- Display: `-0.02em`
- Body: `0`
- Uppercase tracked labels: `+0.18em`
- Mono badges: `+0.10em`

### Spacing

Single 4-px grid: `4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 72`. Don't invent values between these. In Tailwind these map directly to `1 / 2 / 3 / 4 / 5 / 6 / 8 / 10 / 12 / 16 / 18`.

### Radius

`3px` (tags), `4px` (small inputs), `6px` (buttons, inputs), `8px` (cards), `10px` (textarea), `999px` (pills).

### Shadow / depth

Almost no shadows. The aesthetic relies on **borders + background contrast**, not drop shadows. The only shadows used:
- Primary button: `0 1px 0 rgba(255,255,255,0.18) inset, 0 6px 18px rgba(245,165,36,0.18)`
- Status pill dot (active): `0 0 8px rgba(74,222,128,0.5)`

### Motion

- Standard easing: `cubic-bezier(0.2, 0.8, 0.2, 1)` (snappy out)
- Default duration: `120ms` (hover, focus)
- Larger movements: `160ms` (row shift, arrow translate)
- Page entrance: `220ms` with `20ms` stagger for list rows

---

## Background atmosphere

The page sits on:

1. `--bg-0` base
2. Two radial gradients in the top-right and bottom-left corners using `rgba(245,165,36,0.07)` and `0.045` (creates a warm "sunrise" feel without dominating)
3. A fixed SVG noise overlay at `opacity: 0.035` (gives the surface a film-grain quality — small detail, big perceived-quality lift)

This is the single most impactful "looks like a real product" trick. It costs ~50 lines of CSS and changes the perceived polish drastically.

---

## Layout

### Chat view (the page that's currently 75% empty)

```
┌─────────────────────────────────────────────────────────────────┐
│  kloc agent  [BETA]              session 7a9b…  · ← sessions   │  ← sticky header, 48px
├─────────────────────────────────────────────────────────────────┤
│                          │                                      │
│  SESSION                 │                                      │
│  ┌────────────────────┐  │                                      │
│  │ │ session id       │  │   <CopilotChat>                      │
│  │ │ 7a9b34c2-…       │  │     inline, fills the column         │
│  │ └────────────────────┘  │                                      │
│                          │   Messages stream here, the input    │
│  RUNNER STATE            │   bar sits at the bottom of this     │
│  ● fresh                 │   column with a translucent          │
│                          │   backdrop-blur background.          │
│  ARTIFACTS               │                                      │
│  ▪ findings.md           │                                      │
│  ▪ trace.json            │                                      │
│                          │                                      │
│  (empty: italic muted)   │                                      │
│                          │                                      │
└──────────────────────────┴──────────────────────────────────────┘
   ← 288px rail →             ← fluid main column →
```

Three sections in the rail (`SESSION`, `RUNNER STATE`, `ARTIFACTS`) — each with a mono uppercase eyebrow label. The session card has a 2px amber left edge to anchor the brand colour into the layout.

At viewport widths below 880px the rail collapses entirely (`display: none`) and the chat takes the full width — this is a dev tool, mobile is a graceful fallback, not a target.

### Picker view

```
┌─────────────────────────────────────────────────────────────────┐
│  kloc agent  [BETA]                                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│                                                                 │
│   ─── ANALYST CHAT                                              │
│                                                                 │
│   kloc                                                          │
│   agent.                  ← serif italic, 64px, "." in --accent │
│                                                                 │
│   Resume a previous chat or start a new one.                    │
│                                                                 │
│   [+ Start new chat]    24 sessions                             │
│                                                                 │
│   ── RECENT ─────────────────────────────────────               │
│                                                                 │
│   Investigation: login flow                  12 msg · 2h ago    │
│   Session                                    7a9b34c2     →     │
│                                                                 │
│   Cache invalidation walkthrough             8 msg · 5h ago     │
│   Session                                    3c12bb09     →     │
│                                                                 │
│   …                                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

Centred max-width 620px column. Eyebrow label sets context. The display headline ("kloc agent.") is the single biggest visual move — it gives the product a face. The primary action is the amber button; secondary information ("24 sessions") sits next to it in muted mono. Session rows have a hover state that slides the row 6px right and reveals an arrow at the right edge in the accent colour.

---

## Component-level treatment

### Brand mark (header)

```
kloc agent  [BETA]
```

- "kloc" in `--serif` italic, `--text`
- "agent" in `--serif` italic, `--accent` *(visual emphasis: the agent is the product)*
- `[BETA]` in mono, uppercase, tracked `+0.14em`, hairline border, `--text-dim`

### Session card (left rail)

- Background `--bg-2`, 1px `--line` border
- 2px amber edge on the left (the only place that uses raw accent as a fill)
- Eyebrow `SESSION ID` in mono uppercase
- ID in mono, 12.5px, `word-break: break-all`

### Tool card

Retains the current shape and behaviour (denial branch, args/result `<details>`) but:
- 2px accent left edge (red if denied)
- Status badge becomes a pill in `--bg-2`
- Args / result use `<pre>` on `--bg-0` (darker than the card, gives "code well" depth)
- Animated chevron on the `<summary>` (`▸` → `▾`)

### Status pill

```
[ ● FRESH ]
```

Coloured dot + uppercase mono label. Three states map to colour:
- `fresh`, `warm` → green
- `evicted`, `crashed` → red
- (unknown) → `--text-dim`

The active dot has a soft glow (box-shadow) — the only place glows appear.

### Buttons

Three variants:
- `primary` — amber gradient, dark text, soft amber shadow
- `default` — `--bg-2` background, `--line-strong` border, hover `--bg-hover`
- `ghost` — transparent, hairline border

Heights: `30px` (compact), `36px` (default). No giant 48px buttons — this is a dense dev tool.

---

## Why this aesthetic (and not the obvious alternatives)

| Alternative | Why not |
|-------------|---------|
| **Pure flat minimal** (Vercel-style, all white/black, Inter, no character) | Already what we don't have — risks producing the same "AI startup template" look. We need *one* distinctive choice. |
| **Maximalist (Linear-style multi-pane, lots of chrome, gradients, accents everywhere)** | Wrong for an LLM chat. The chat needs to breathe. |
| **Retro terminal (green-on-black, monospace everywhere)** | Strong but limits readability for chat messages, which are inherently long-form text. |
| **Purple gradient on white** | Cliché. Explicitly listed as anti-pattern in the design brief. |
| **Light theme primary** | The product is a dev tool used in IDE-adjacent contexts. Dark first matches the audience. A light variant can be added later via a single `[data-theme]` switch. |

The "editorial terminal" direction lands between these: dark and dense like a dev tool, but with the *one* refined typographic choice (serif italic display) that signals taste.

---

## Alternatives (open for redirect)

If the serif italic direction doesn't fit, the framework above can be retuned to:

- **Geometric / Swiss** — replace `Instrument Serif` with `Söhne` or `Funnel Display`, drop the warm gradients, switch accent to a sharper electric blue (`#2563eb`). More technical, less editorial.
- **Off-white / paper** — invert to a near-white background with warm ink-blacks, keep the amber accent. Reads as "engineering notebook" rather than "terminal". Recommended only if a light theme is the primary target.
- **High-contrast brutalist** — neutral palette, oversized monospace headings, no gradients, no shadows, sharp 0-radius corners. Strong choice if "kloc" wants to lean into the "machine analyst" identity.

A 30-minute sketch of any of these can be produced before committing.
