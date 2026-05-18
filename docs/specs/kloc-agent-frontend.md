# Feature: kloc-agent frontend (rewrite)

> Rewrite of `kloc-agent/frontend/` from scratch. The existing directory has
> been intentionally wiped; this spec is the full scope for the new build.
> Authoritative behaviour: [`docs/behavior.xml`](../usdl/behavior.xml). Visual
> contract: [`docs/specs/ui/kloc-analyst.html`](./ui/kloc-analyst.html)
> (light theme, pixel-level mockup) and
> [`docs/specs/ui/img_1.png`](./ui/img_1.png) (dark theme reference).
> Backend behaviour and acceptance for the agent loop remain governed by
> [`kloc-agent-poc.md`](./kloc-agent-poc.md); this document only covers
> what the analyst sees in the browser.

---

## Goals

Deliver a single-page Next.js application called "kloc analyst" that lets a
single analyst browse prior Sessions, start a new Session, ask the
Assistant natural-language questions against the Indexed PHP Codebase, and
recover seamlessly from transient network drops. The UI must match the
mockup pixel-for-pixel in light mode, render an equivalent dark theme that
mirrors `img_1.png`, stream Assistant replies progressively from the
backend over AG-UI / SSE, and surface Tool Calls, Tool Denials, and
Artifacts in line with the formal behaviour spec.

---

## In Scope

- Two top-level surfaces: a left **session rail** (browse / new / resume)
  and a right **conversation surface** (read history + ask + observe
  streamed reply).
- Direct integration with `@ag-ui/client 0.0.42` via an in-app
  Next.js route handler (`src/app/api/agent-proxy/route.ts`) that forwards
  to the FastAPI backend. CopilotKit is dropped.
- Full client-side rendering of all AG-UI 0.1.18 event types relevant to
  the five use-cases (text deltas, tool-call lifecycle, artifact
  attachment, run lifecycle, stream resume).
- Light theme matching `kloc-analyst.html` token-for-token; dark theme
  matching the supplied screenshot — both selectable via system
  preference and an explicit toggle (`prefers-color-scheme` + persisted
  override in `localStorage`).
- Self-hosted **Geist** (sans) and **JetBrains Mono** (mono) loaded via
  `next/font` — no `fonts.googleapis.com` runtime dependency.
- Tailwind 4 design tokens defined as CSS custom properties; identical
  token names to the mockup (`--color-ink`, `--color-canvas-rail`, etc.)
  with parallel `*-dark` values.
- `<html lang="en">` set on the root layout for assistive technology
  (NFR `nfr.document-language`).
- Reconnect-on-drop using `last_event_id` cursor replay against
  `GET /v1/sessions/{id}/stream`.
- 500-message history page on resume (NFR `nfr.history-page-size`).
- Disabled / closed-session state for the input bar (rule
  `beh.rule.closed-session-immutable`).

## Out of Scope

- Authentication, multi-tenant analyst handling, user profile UI (PoC is
  single hardcoded analyst).
- Branching messages (`messages.parent_message_id` exists in the schema
  but the UI ignores it).
- Editing or deleting prior Messages.
- Artifact lifecycle UI (deletion, expiry, listing). The UI only
  surfaces names produced during a Session, with a download affordance.
- Search inside Sessions, search across Sessions, full-text filtering
  (the magnifying-glass icon in the mockup is decorative for this
  milestone — it MAY be wired up later but is not required).
- Settings, preferences, or admin pages beyond the theme toggle.
- Mobile / narrow-viewport layout (target: ≥1024 px desktop width).
- Token-window summarisation, tool-call retry, branching alternative
  replies.
- Multi-tab session sharing / cross-tab sync. Two tabs of the same
  Session may temporarily diverge until the next reconnect cycle.

---

## Acceptance Criteria

Acceptance is grouped by the five use-cases in `behavior.xml`. Each
use-case lists one or more Gherkin scenarios. All scenarios MUST pass
under interactive Chrome browser automation (see "Browser QA
requirement" below); static snapshot review alone is insufficient.

### UC1 — browse-sessions (`beh.browse-sessions`)

```gherkin
Scenario: Analyst with prior Sessions opens the landing surface
  Given the analyst has three prior Sessions in the backend
  When they navigate to the application root "/"
  Then the left rail renders three Session entries
  And the most-recently-updated Session appears first
  And each entry shows the Session title, the short identifier
      (first 7 characters of the UUID, in mono), the Message count,
      and the last-activity time
  And entries are grouped by recency ("Today" / "Earlier") with mono
      uppercase headers matching the mockup
```

```gherkin
Scenario: Empty state
  Given the analyst has no prior Sessions
  When they navigate to "/"
  Then the rail shows an empty-state indication
  And the "New session" button remains visible and enabled
```

```gherkin
Scenario: Retrieval failure
  Given GET /v1/sessions returns 5xx
  When the analyst navigates to "/"
  Then the rail shows an inline error indication naming the failure
  And the "New session" button remains visible and enabled
```

### UC2 — start-new-session (`beh.start-new-session`)

```gherkin
Scenario: Create a new Session from the landing surface
  Given the analyst is on the landing surface
  When they click "New session"
  Then the frontend issues POST /v1/sessions
  And on 201 the conversation surface for the new Session opens
  And the URL reflects the new session_id (e.g. /s/{session_id})
  And the thread is empty (zero prior Messages)
  And the input is focused and ready to accept a question
```

```gherkin
Scenario: Creation failure
  Given POST /v1/sessions returns 5xx
  When the analyst clicks "New session"
  Then the analyst remains on the landing surface
  And an error indication is shown
  And no navigation occurs
```

### UC3 — resume-session (`beh.resume-session`)

```gherkin
Scenario: Resume a prior Session
  Given a Session with five prior Messages exists
  When the analyst clicks that Session in the rail
  Then the URL changes to /s/{session_id}
  And the conversation surface opens with the Session's title and short id
      in the header
  And prior Messages render in order, oldest first
  And the active Session entry in the rail shows the active styling
      (white background, ring, pulsing blue dot — exact tokens from mockup)
```

```gherkin
Scenario: Large history is paginated
  Given a Session with more than 500 prior Messages
  When the analyst opens it
  Then the most-recent 500 Messages are fetched in a single request to
      GET /v1/sessions/{id}/messages?limit=500
  And an indication is rendered that older Messages exist and can be
      loaded on demand
```

```gherkin
Scenario: Return to the list without losing state
  Given the analyst is on a resumed Session
  When they navigate back to the landing surface (via the in-header back
      affordance or the URL)
  Then the chosen Session's loaded history is preserved in client cache
  And re-selecting it does not refetch unchanged Messages
```

### UC4 — ask-assistant (`beh.ask-assistant`)

```gherkin
Scenario: Submit a question and see a progressive reply
  Given an OPEN Session and the analyst has typed a question
  When the analyst submits (click send, press Enter, or press ⌘↵)
  Then the analyst's Message renders immediately in the thread
  And the frontend opens a POST /v1/sessions/{id}/stream connection with
      Accept: text/event-stream and a RunAgentInput body
  And TEXT_MESSAGE_CONTENT deltas append progressively to a single
      Assistant Message bubble
  And the bubble shows the blinking caret indicator while streaming
  And on RUN_FINISHED the bubble's blinking caret is removed
  And the Assistant Message is finalized in the thread
```

```gherkin
Scenario: Tool Call indicator lifecycle
  Given the Assistant is replying
  When a TOOL_CALL_START event arrives with name "search_code"
  Then a Tool Call card renders in the Assistant bubble with the tool
      name (mono) and arguments (mono, truncated)
  And the card shows the spinning loader icon while running
  When TOOL_CALL_END arrives
  Then the card transitions to the "done" visual state (green check icon,
      neutral surface) and shows the result meta (e.g. "14 results")
```

```gherkin
Scenario: Tool Denial is visually distinct
  Given the Assistant requests a Tool Call that policy denies
  When an AG-UI CUSTOM event arrives with
      name="ToolCallDenied"
      value={"toolCallId": "<id>", "reason": "<denial reason>"}
  Then the frontend matches the toolCallId to the in-flight Tool Call card
  And the card renders with the danger token surface
      (background var(--color-danger-bg), border var(--color-danger-line),
      ink var(--color-danger-ink)) AND a "DENIED" label badge
  And the denial reason from value.reason is shown in place of the result meta
  And the affordance is identifiable without colour (label is present)
```

> Implementation note: `ag-ui-protocol 0.1.18` does not define a first-class
> `TOOL_CALL_DENIED` event. The runner emits this state as an AG-UI **CUSTOM
> event** (`type: "CUSTOM"`, `name: "ToolCallDenied"`) from
> `runner/hooks/audit.py` alongside the existing HMAC webhook flow that
> writes the audit row — the webhook persists the denial server-side; the
> CUSTOM event surfaces it to the analyst over SSE.

```gherkin
Scenario: Artifact attachment surfaces in the thread
  Given the Assistant produces an Artifact during a reply
  When an AG-UI CUSTOM event arrives with
      name="ArtifactAttached"
      value={"artifactId": "<uuid>", "filename": "<name>",
             "sizeBytes": <int>, "mimeType": "<mime>"}
  Then a named file chip renders inside the Assistant bubble
  And the chip carries value.filename (mono), value.sizeBytes formatted as
      a human-readable size, and a download icon
  And clicking the chip navigates to GET /v1/artifacts/{value.artifactId}
      which 302s to a presigned download URL
```

> Implementation note: artifact surfacing is also an AG-UI **CUSTOM event**
> (`type: "CUSTOM"`, `name: "ArtifactAttached"`) emitted by
> `runner/hooks/audit.py` after the runner uploads the file and registers it
> via the `artifact_registered` webhook. The webhook persists
> `artifact_metadata` server-side; the CUSTOM event makes the artifact
> visible to the analyst in real time.

```gherkin
Scenario: Closed Session rejects new questions
  Given the Session is CLOSED
  When the analyst opens the conversation surface
  Then the input bar is disabled with a "this session is closed"
      indication
  And the send button is non-interactive
```

```gherkin
Scenario: Assistant failure
  Given the runner crashes mid-reply (RUN_ERROR)
  When the SSE stream closes without RUN_FINISHED
  Then the analyst's submitted Message remains in the thread (it was
      persisted before the runner was contacted)
  And the Assistant bubble is marked unfinalized with a retry affordance
```

### UC5 — recover-from-disconnect (`beh.recover-from-disconnect`)

```gherkin
Scenario: Reconnect within the recovery window
  Given the analyst was observing an in-flight Assistant reply
  And the analyst's network drops momentarily
  When the network returns within the ExecutionRegistry replay-buffer
      retention window (default 5 minutes after RUN_FINISHED, ring
      capacity 10 000 events per run)
  Then the frontend issues
      GET /v1/sessions/{id}/stream?run_id=...&last_event_id=<cursor>
  And buffered events emitted during the disconnect are replayed in order
  And live streaming resumes from the current point
  And no Tool Call card and no Artifact chip is missing from the thread
```

> Clarification on timers: the **ExecutionRegistry retention** (≈5 min
> after `RUN_FINISHED`, 10 000-event ring) is the replay window that this
> scenario depends on. The **`RUNNER_HEARTBEAT_TIMEOUT_S=30`** timer is a
> separate concern — it governs runner liveness (heartbeat watcher kills
> the container if no heartbeat arrives within 30 s) and is NOT the
> reconnect window. The two timers must not be conflated.

```gherkin
Scenario: Assistant completed while disconnected
  Given the Assistant finished its reply during the disconnect
  When the analyst reconnects
  Then the finalized Assistant Message is visible in full
  And the input bar is idle and ready for the next question
```

```gherkin
Scenario: Recovery window exceeded
  Given the analyst was disconnected longer than the execution-registry
      retention window
  When the analyst reconnects
  Then the frontend falls back to GET /v1/sessions/{id}/messages and
      renders the Session's persisted Messages (including any finalized
      Assistant Message)
  And no intermediate streaming events are replayed
```

---

## Non-Functional Requirements

| NFR | Source (behavior.xml) | Quality | Measure | Threshold |
|-----|-----------------------|---------|---------|-----------|
| Progressive rendering | `nfr.progressive-rendering` | Usability | Time from submit to first visible token, p95 | < 1000 ms |
| History page size | `nfr.history-page-size` | Performance | Messages per resume request | ≤ 500 in a single page |
| Disconnect recovery | `nfr.disconnect-recovery` | Availability | Reconnects within window that restore the stream without missing events | ≥ 99.9 % |
| Tool denial affordance | `nfr.tool-denial-affordance` | Accessibility | Tool Denial is conveyed by both colour AND a label | Both must be present; label suffices alone for colour-blind users |
| Document language | `nfr.document-language` | Accessibility | `<html lang>` present and correct on every page | `lang="en"` |

Additional engineering constraints (not from behavior.xml, but binding on
this rewrite):

- Initial JS bundle (compressed) on the landing surface ≤ 200 KB. No
  CopilotKit, no `@copilotkit/react-*` packages of any kind.
- Next.js 16.0.8, React 19.2.1, TypeScript 5.6 strict, Tailwind 4,
  `@ag-ui/client 0.0.42` — versions are locked; no upgrades.
- Self-hosted fonts via `next/font`; no remote font fetch at runtime.
- `next.config.ts` keeps `output: "standalone"` so the existing
  `frontend/Dockerfile` deploys unchanged.

---

## Visual Acceptance

The light-theme mockup `docs/specs/ui/kloc-analyst.html` is the
authoritative visual contract. The rewrite MUST adopt the mockup's
design tokens verbatim — copy them into Tailwind 4 `@theme` and use the
same custom-property names in the implementation so reviewers can grep
the codebase against the mockup:

| Token | Light value | Dark value (from `img_1.png`) |
|-------|-------------|-------------------------------|
| `--color-ink` | `#0c0a09` | near-white (`#f5f5f4`) |
| `--color-ink-muted` | `#57534d` | `#a8a29e` |
| `--color-ink-faint` | `#a8a29e` | `#78716c` |
| `--color-line` | `#e7e5e3` | `#27272a` |
| `--color-line-strong` | `#d6d3d0` | `#3f3f46` |
| `--color-canvas` | `#ffffff` | `#1c1917` |
| `--color-canvas-rail` | `#faf9f7` | `#18181b` |
| `--color-canvas-sunk` | `#f5f4f1` | `#27272a` |
| `--color-accent` | `#2b5cff` | `#7c92ff` |
| `--color-success` | `#15803d` | `#22c55e` |
| `--color-warning` | `#b45309` | `#f59e0b` |
| `--color-danger-bg` | `#fef2f2` | `#3f1d1d` (the sunk red panel in `img_1.png`) |
| `--color-danger-line` | `#fecaca` | `#7f1d1d` |
| `--color-danger-ink` | `#991b1b` | `#fca5a5` |

(QA: dark-mode hex values are guidance derived from `img_1.png`; verify
visually against the reference image rather than treating them as
pixel-equivalence requirements.)

Typography:
- Sans: **Geist** weights 400 / 500 / 600, self-hosted via `next/font`.
- Mono: **JetBrains Mono** weights 400 / 500, self-hosted via
  `next/font`, with `font-feature-settings: "ss01", "ss02"` on mono
  elements.

Layout invariants (lifted from the mockup):
- Left rail: fixed `260 px` width, full-height column,
  `--color-canvas-rail` background, right border `--color-line`.
- Rail header: brand chip (`6 px` rounded, ink fill, sparkle glyph),
  `13 px` tracking-tight wordmark "kloc", `v0.1` in mono `10 px`
  ink-faint, "New session" button below at full width with
  `--color-line-strong` border.
- Session list: grouped by recency, group header mono `9.5 px` letter-
  spacing `0.12 em` uppercase ink-faint; active item has white surface,
  `1 px` ring `--color-line-strong`, pulsing accent dot.
- Rail footer: database icon + repo handle (mono `11 px` ink-muted) +
  file-count subtitle (mono `10 px` ink-faint).
- Conversation surface: max-width `680 px`, centered, vertical scroll;
  header with back-arrow, title (`14 px` medium), subtitle in mono
  `11 px` ink-muted, search + menu icon buttons right-aligned.
- Analyst row: round avatar `28 px` with `--color-accent / 10` tint,
  initials in mono `10 px` accent; meta row "Analyst · HH:MM".
- Assistant row: square avatar `28 px` rounded `6 px` ink fill, sparkle
  glyph; meta row "kloc analyst · HH:MM".
- Tool Call card: border `1 px` `--color-line`, rounded `6 px`,
  vertical padding `6 px`; icon → name (mono `12 px` medium) → args
  (mono `11 px` ink-muted, truncated) → meta right-aligned. Denied
  variant swaps in danger tokens AND renders an outlined "DENIED" badge
  on the right.
- Artifact chip: inline-flex, `2.5 / 1.5` padding, `--color-canvas-rail`
  background, `--color-line` border, file icon + filename (mono `12 px`)
  + size (mono `10.5 px` ink-muted) + download icon.
- Input bar: full-width pill-bar inside the centered `680 px` column,
  white surface, `1 px` `--color-line-strong` border, focus ring
  `--color-accent / 15`, send button square `28 px` ink fill on the
  right, ⌘↵ hint between input and button. Helper line below input in
  mono `10 px` ink-faint.

QA MUST visually compare the live UI to `kloc-analyst.html` (light) and
`img_1.png` (dark). Deviations in token values, spacing, type-scale, or
component composition are defects.

---

## Browser QA Requirement

This feature CANNOT be signed off by reading code, by reading rendered
HTML, or by reviewing screenshots produced from the dev server alone. QA
MUST exercise every Gherkin scenario above (UC1–UC5) interactively in a
real Chrome browser using browser automation. Specifically:

- The QA agent uses Chrome browser automation tooling to drive the
  running frontend at `http://localhost:3000` with the FastAPI backend
  at `http://localhost:8000`.
- For each Gherkin scenario, QA performs the user actions (click,
  type, submit, drop network) and observes the resulting DOM, network
  traffic (SSE frames), and visual rendering.
- QA MUST verify both light theme (default) and dark theme by toggling
  `prefers-color-scheme` (DevTools emulation) and via the in-app theme
  toggle, comparing the live UI against `kloc-analyst.html` and
  `img_1.png` respectively.
- QA MUST induce a network disconnect (DevTools throttling → offline)
  mid-stream and verify the reconnect / cursor-replay scenarios under
  UC5 actually work end-to-end, not just in isolation.
- QA MUST verify the Tool Denial card by forcing a denial path (e.g. a
  backend Policy override returning `{decision: "deny"}`) and inspect
  the rendered card for BOTH colour distinction AND the "DENIED"
  label.
- QA MUST confirm `<html lang="en">` is present in the served document.

Static review by the reviewer agent is complementary — it checks code
quality, types, and structure — but it is not a substitute for
interactive browser QA.

---

## Tool-Call Card Visible States

`behavior.xml` defines three Tool Call statuses: `in-progress`,
`executing`, and `complete`. The frontend collapses these into **three
visible card states** matching the mockup. This normalization is
intentional — `in-progress` and `executing` are not meaningfully
distinguishable to the analyst (both mean "still running") and the
mockup carries a single running visual.

| FE state | behavior.xml status | Trigger | Icon | Tokens |
|----------|---------------------|---------|------|--------|
| `running` | `in-progress`, `executing` | `TOOL_CALL_START` received, no `TOOL_CALL_END` and no `ToolCallDenied` CUSTOM event yet | spinning loader, warning ink | neutral surface (`bg-white`, border `--color-line`) |
| `done` | `complete` | `TOOL_CALL_END` received (the result is attached to the card) | check, success ink | neutral surface (same as `running`) |
| `denied` | (denial, distinct from `complete`) | `ToolCallDenied` CUSTOM event received whose `toolCallId` matches the card | ban / forbid glyph, danger ink | danger surface (`bg --color-danger-bg`, border `--color-danger-line`), plus outlined "DENIED" label badge on the right |

The `running` → `done` transition is on `TOOL_CALL_END`; the `running` →
`denied` transition is on the `ToolCallDenied` CUSTOM event. Once a
card has entered `done` or `denied`, no further transitions are
applied to it.

---

## Backend Pre-requisites for Browser QA

Before QA exercises the Gherkin scenarios in Chrome, the backend stack
must be up and configured. This section is informative for QA — it does
not change the frontend contract.

**Compose target** (the canonical bring-up for QA):

```bash
docker compose -f docker-compose.yml up -d backend postgres minio
```

**Required environment variables on the backend container:**

- `LLM_PROVIDER=gemini` plus `GEMINI_API_KEY=<key>`
  *(or `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=<key>`)*
- `KLOC_STUB_MODE=true` is acceptable for QA paths that do **not** need
  a real LLM (UC1 / UC2 / UC3 / UC5-recovery-window-exceeded). Skips
  provider-key validation at boot. UC4 scenarios that require real
  Assistant output should run with a real provider key.
- `KLOC_CORS_ALLOW_ORIGINS=http://localhost:3000` — required for the
  browser to call the backend API and open the SSE stream cross-origin.
- `KLOC_DENY_TOOLS=read_file` — pre-configures the policy layer to deny
  any `read_file` tool call. This makes UC4's "Tool Denial is visually
  distinct" scenario triggerable simply by asking the Assistant to read
  a file (e.g. "read the .env file").
- `KLOC_MCP_URL=<url>` — Streamable-HTTP MCP endpoint of the running
  kloc-intelligence stack (required only for UC4 scenarios that need
  real MCP tool calls; for UC1–UC3 / UC5 with `KLOC_STUB_MODE=true`
  this can be unset).

**Optional seeding for resume / browse scenarios.** UC1 (browse), UC3
(resume), and UC5 (recovery-window-exceeded) need at least one
pre-existing Session with messages. QA may seed via the REST API
before opening the browser:

```bash
SID=$(curl -s -XPOST http://localhost:8000/v1/sessions \
  -H 'Content-Type: application/json' \
  -d '{"title":"Audit auth flow"}' | jq -r .session_id)
curl -s -XPOST http://localhost:8000/v1/sessions/$SID/messages \
  -H 'Content-Type: application/json' \
  -d '{"role":"user","content":"Where is the JWT secret loaded?"}'
```

A formal seed-fixture script is **not** required by this spec — QA
chooses how to seed, as long as the conditions in each Gherkin `Given`
clause are satisfied before the `When`.

**Frontend dev server.** Run `npm run dev` from `kloc-agent/frontend/`
and open `http://localhost:3000`. The Next.js route handler at
`/api/agent-proxy` forwards to `BACKEND_URL` (default
`http://localhost:8000`).

---

## API Endpoints Consumed

Lifted from `src/api/` in the backend. The frontend talks to exactly
these routes (all under `/v1`); CORS allows the frontend origin.

| Method | Path | Purpose | Use-case |
|--------|------|---------|----------|
| `GET` | `/v1/sessions?include_closed=false` | List the analyst's Sessions, newest first, with `message_count`. Drives the rail. | UC1 |
| `POST` | `/v1/sessions` | Create a new OPEN Session; returns `{session_id, created_at}`. | UC2 |
| `GET` | `/v1/sessions/{id}` | Fetch Session detail (title, runner_state, closed_at) for the header and closed-state gate. | UC3, UC4 |
| `GET` | `/v1/sessions/{id}/messages?after=<seq>&limit=500` | Page of prior Messages on resume; ordered by `seq`. | UC3, UC5 (fallback) |
| `POST` | `/v1/sessions/{id}/messages` | Append a user Message (persist-first). Returns `{run_id, message_id, stream_url}`. May be used by the frontend if it wants the persist step decoupled from the SSE open; otherwise the POST `/stream` route persists on its behalf. | UC4 |
| `POST` | `/v1/sessions/{id}/stream` | Body is `RunAgentInput`; backend persists the user Message, spawns/reuses the runner, streams AG-UI events back as SSE. Primary submit path. | UC4 |
| `GET` | `/v1/sessions/{id}/stream?run_id=<rid>&last_event_id=<seq>` | Cursor replay + live tail of an in-flight run. Used after a transient disconnect. | UC5 |
| `POST` | `/v1/sessions/{id}/close` | Close the Session. Optional for the rewrite; UI need not expose this control in M0 but MUST handle a Session whose `closed_at` is non-null. | UC4 (gate) |
| `GET` | `/v1/artifacts/{artifact_id}` | 302 redirect to a presigned MinIO URL. Target of the artifact chip's download click. | UC4 |
| `GET` | `/health` | Liveness probe. Optional; may be used by the rail to show a banner when the backend is down. | UC1 alt-flow |

The frontend's own `src/app/api/agent-proxy/route.ts` route handler
adapts AG-UI's `HttpAgent` (from `@ag-ui/client 0.0.42`) to a
server-side SSE forward, so the browser does NOT need to know about
backend auth, CORS, or AG-UI envelope details directly — but
functionally every byte that crosses the wire maps to one of the
endpoints above.

---

## Links

- [`docs/behavior.xml`](../usdl/behavior.xml) — formal use-case / rule /
  invariant / NFR spec (authoritative for behaviour).
- [`docs/specs/ui/kloc-analyst.html`](./ui/kloc-analyst.html) — light
  theme pixel-level mockup (authoritative for visual tokens & layout).
- [`docs/specs/ui/img_1.png`](./ui/img_1.png) — dark theme reference
  image.
- [`docs/specs/kloc-agent-poc.md`](./kloc-agent-poc.md) — PoC system
  spec (backend acceptance, session lifecycle, runner contract).
- [`CLAUDE.md`](../../CLAUDE.md) — stack pins, conventions, comment
  policy.
