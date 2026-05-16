# Performance — Re-render & Render Cost

Issues that affect render-time CPU, allocation churn, and React's ability to skip work.

---

## P-1 — Inline `style={{…}}` objects on every render *(medium)*

**Rule:** `rerender-memo-with-default-value`, `rendering-hoist-jsx`
**Files:** `src/app/page.tsx:84-113`, `:153-247`; `src/components/AgentBody.tsx:92-101`; `src/components/Composer.tsx:33-43`; `src/components/ToolCallCard.tsx:54-110`

Every component allocates fresh style objects on each render. Beyond the GC churn, each `<button style={…}>` produces a new prop reference, defeating any future `React.memo` wrapper and forcing the DOM reconciler to re-apply equal-valued style props.

```tsx
// page.tsx:84 — re-allocates every render
<header style={{ padding: "16px 24px", borderBottom: "...", ... }}>
```

**Fix:** Move to `globals.css` / a CSS module. Use `className` only. For the few dynamic values (`busyId !== null ? "wait" : "pointer"`), keep a single small `style` object with just that property, or toggle a class.

---

## P-2 — `useCopilotAction` render callback recreated each render *(low–medium)*

**Rule:** `rerender-no-inline-components`
**File:** `src/components/AgentBody.tsx:70-90`

```tsx
useCopilotAction({
  name: "*",
  render: ({ name, args, status, result }) => (
    <ToolCallCard ... />
  ),
});
```

`render` is a fresh function on every parent render and the JSX inside *defines a render-time element* identifying as a child. If CopilotKit ever wraps this in `React.memo`, the new function reference busts caching.

**Fix:** Define `RenderToolCall` at module scope and pass the stable reference:

```tsx
function RenderToolCall(p: { name: string; args: Record<string, unknown>; status: string; result?: unknown }) {
  return <ToolCallCard {...p} args={p.args ?? {}} />;
}
useCopilotAction({ name: "*", render: RenderToolCall });
```

---

## P-3 — Seed effect re-runs on every message append *(low)*

**Rule:** `rerender-dependencies`, `rerender-defer-reads`
**File:** `src/components/AgentBody.tsx:50-68`

```tsx
useEffect(() => {
  if (seededRef.current) return;
  ...
}, [initialMessages, messages.length, setMessages]);
```

The `messages.length` dep means the effect (and the surrounding `useCopilotMessagesContext()` subscription) re-fires on every assistant token append. The guard short-circuits, but the component also re-renders every time `messages` updates even though it only needs the *initial* snapshot.

**Fix:** Drop the subscription after seeding — read messages once via a ref-bound getter, or split a small `<SeedMessages />` child that unmounts after seeding. At minimum, gate with a single-shot effect:

```tsx
useEffect(() => {
  if (seededRef.current || !initialMessages?.length) return;
  seededRef.current = true;
  setMessages(initialMessages.map(toGqlMessage));
}, []); // intentional: one-shot
```

---

## P-4 — `new Date(...).toLocaleString()` per row, per render *(low)*

**Rule:** `js-cache-function-results`
**File:** `src/app/page.tsx:237`

```tsx
{new Date(s.updated_at).toLocaleString()}
```

Allocates a `Date` and runs locale formatting for every session row on every render of `SessionPicker`. With dozens of sessions and any state change (busyId, error), this adds up.

**Fix:** Memoize per-row formatting, or precompute when `sessions` arrives:

```tsx
const formatted = useMemo(
  () => sessions?.map((s) => ({ ...s, updatedLabel: new Date(s.updated_at).toLocaleString() })),
  [sessions],
);
```

---

## P-5 — Inline list-item closures *(low)*

**Rule:** `rerender-memo`
**File:** `src/app/page.tsx:212` (`onClick={() => onPick(s)}`)

Each row gets a fresh closure per render. Currently fine (no `memo` downstream), but combined with P-1 this prevents any future memoization of `<SessionRow>`. Extracting a `SessionRow` component that calls `onPick(id)` internally fixes both at once.

---

## P-6 — `useCoAgent({ initialState })` rebuilds inline object *(low)*

**Rule:** `rerender-memo-with-default-value`
**File:** `src/components/AgentBody.tsx:37-40`

```tsx
const { state } = useCoAgent<KlocAgentState>({
  name: AGENT_NAME,
  initialState: { artifacts: [] },
});
```

`{ artifacts: [] }` is reallocated each render. CopilotKit likely captures it once, but the reference changes every render in the meantime. Hoist to module scope:

```tsx
const INITIAL_AGENT_STATE: KlocAgentState = { artifacts: [] };
```

---

## P-7 — Conditional render uses `&&` against numbers *(low)*

**Rule:** `rendering-conditional-render`
**File:** `src/components/AgentBody.tsx:106`

```tsx
{state?.artifacts && state.artifacts.length > 0 && (<ul>...)}
```

Safe here (the operands are not falsy-numeric). No fix needed — flagged only because the Vercel rule recommends ternaries for clarity. Keep as-is.

---

## Out-of-scope / non-issues

- **`SessionPicker` defined outside `HomePage`** — already follows `rerender-no-inline-components`. Good.
- **`useState` with primitives** — no lazy-init issue (`rerender-lazy-state-init` not applicable).
- **No animated SVGs / long lists requiring `content-visibility`.**
