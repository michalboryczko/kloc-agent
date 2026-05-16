# Client-Side Data Fetching

Issues in `src/lib/api.ts` and its consumers.

---

## D-1 — `cancelled` flag without `AbortController` *(low–medium)*

**Rule:** `client-swr-dedup` (adjacent)
**File:** `src/app/page.tsx:29-44`

```tsx
useEffect(() => {
  if (picked) return;
  let cancelled = false;
  listSessions()
    .then((res) => { if (!cancelled) setSessions(res.sessions); })
    .catch(...);
  return () => { cancelled = true; };
}, [picked]);
```

The flag prevents the state update but the underlying `fetch` keeps running — wasted bandwidth, wasted backend cycles, plus a CORS/cookie attack surface if the request is in flight when the user logs out elsewhere.

**Fix:** Plumb `AbortSignal` through `listSessions`/`listMessages`:

```ts
export async function listSessions(opts: {...} = {}, signal?: AbortSignal): Promise<SessionList> {
  const res = await fetch(url, { signal });
  ...
}

// caller
useEffect(() => {
  if (picked) return;
  const ctrl = new AbortController();
  listSessions({}, ctrl.signal).then(...).catch((e) => {
    if (e.name === "AbortError") return;
    setError(...);
  });
  return () => ctrl.abort();
}, [picked]);
```

---

## D-2 — No SWR / no dedup / no revalidation *(low)*

**Rule:** `client-swr-dedup`
**Files:** `src/app/page.tsx:29-44`, `src/lib/api.ts`

`listSessions` is called once on mount and re-run only when the user backs out of a chat. If two components ever need the same data, each fires its own fetch; if the user keeps the picker open the list never refreshes (a session opened in another tab won't appear).

**Fix (when this matters):** Adopt `swr` (or `@tanstack/react-query`) for the picker:

```tsx
const { data, error } = useSWR("/v1/sessions", () => listSessions());
```

You get free dedup, focus-revalidate, and `mutate("/v1/sessions")` after `createSession()`.

---

## D-3 — Error state never cleared *(low — UX)*

**File:** `src/app/page.tsx:46-68`

```tsx
async function pickExisting(s: SessionListItem) {
  setBusyId(s.id);
  try {
    const page = await listMessages(s.id, { limit: 500 });
    setPicked({ ... });
  } catch (e) {
    setError(...);
  } finally {
    setBusyId(null);
  }
}
```

A stale "failed to load sessions" message remains visible while the user successfully retries via another path. Call `setError(null)` at the top of each handler, and also when the user navigates from picker → chat → picker.

---

## D-4 — `limit: 500` hard-coded; no pagination UI *(low)*

**File:** `src/app/page.tsx:49`, `src/lib/api.ts:84-93`

`listMessages` supports an `after` cursor and `has_more`/`next_cursor` are exposed, but the caller asks for 500 in one shot and never reads `next_cursor`. For long-running analyst sessions this both (a) loads more than needed for first paint and (b) silently truncates history beyond 500.

**Fix:** Either accept the truncation explicitly with a "Load older…" affordance (read `page.has_more`), or fetch the most recent N for the initial seed and lazy-load earlier pages on scroll-to-top.
