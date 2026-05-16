# Accessibility

Issues that affect screen-reader users, keyboard users, and low-vision users.

---

## A-1 — `<textarea>` has no accessible name *(medium)*

**File:** `src/components/Composer.tsx:21-43`

```tsx
<textarea value={value} onChange={...} placeholder="Ask kloc…" ... />
```

`placeholder` is **not** a label. Screen readers may announce it inconsistently, and once the user types it disappears. Composer is currently unused (see `code-quality.md` D-Q-1) but if it gets adopted it ships an inaccessible input.

**Fix:**

```tsx
<label>
  <span className="sr-only">Ask kloc</span>
  <textarea aria-label="Ask kloc" ... />
</label>
```

---

## A-2 — Low-contrast text via `opacity` *(medium)*

**Files:** `src/app/page.tsx:109` (`opacity: 0.7`), `:155` (`0.75`), `:185`/`:189` (`0.6`), `:234` (`0.6`); `src/components/AgentBody.tsx:98` (`0.8`); `src/components/ToolCallCard.tsx:72`/`:76`/`:93` (`0.65`/`0.8`)

Applying `opacity` to text inherits the background-mixed luminance — at `0.6` on a light or dark theme, contrast often drops below WCAG AA (4.5:1). And because the app uses inline styles in both light- and dark-favorable colors (`rgba(120,120,120,…)`), the actual ratio is unpredictable.

**Fix:** Use explicit foreground colors with tested contrast (`color: var(--text-muted)` defined in `globals.css` to meet ≥4.5:1 in both themes). Reserve `opacity` for non-text visuals.

---

## A-3 — "← sessions" back button relies on glyph *(low)*

**File:** `src/app/page.tsx:93-106`

```tsx
<button type="button" onClick={() => setPicked(null)}>← sessions</button>
```

Has visible text ("sessions") so screen readers do get *something*, but "left-arrow sessions" can read awkwardly. Add an explicit `aria-label="Back to sessions"`.

---

## A-4 — Session row uses `<button>` inside `<li>` for navigation, no semantic state *(low)*

**File:** `src/app/page.tsx:210-241`

When `busyId === s.id`, the button shows "loading…" but doesn't expose state to assistive tech. Add `aria-busy={busyId === s.id}` and `aria-disabled` (already handled by `disabled`).

Also: `cursor: "wait"` is on every button when *any* is busy, which is misleading to mouse users — only the actually busy one should show wait.

---

## A-5 — Heading hierarchy: two `<h1>`s across states *(low)*

**Files:** `src/app/page.tsx:108` (chat header `<h1>kloc-agent</h1>`), `:154` (picker `<h1>kloc-agent</h1>`)

Only one `<h1>` is in the DOM at a time, so this is technically valid. Still worth standardizing the page title in `metadata` and using `<h1>` for the picker but `<h2>` for "session: {id}" in the chat state.
