# Implementation plan: kloc-agent frontend (rewrite)

> Companion plan to `docs/specs/kloc-agent-frontend.md`. The spec is the
> authoritative *what*; this plan is the authoritative *how*. Use-case
> IDs (`UC1`–`UC5`) and acceptance scenarios are quoted from the spec
> and from `docs/behavior.xml`.
>
> Working directory for all paths below: `kloc-agent/frontend/` unless
> noted otherwise.

---

## 0. Decisions baked in by this plan

These are confirmed from backend exploration (Phase 1) and the PM spec.
The plan rests on them and they must not be revisited without checking
the spec and behavior.xml first.

1. **No CopilotKit.** `@ag-ui/client 0.0.42` is consumed directly. The
   Next.js `agent-proxy` route is kept solely as an SSE forward (so the
   browser never needs to know about CORS / backend headers / replay
   semantics — but it does NOT translate envelopes; it just pipes bytes
   from `POST /v1/sessions/{id}/stream` and `GET /v1/sessions/{id}/stream`
   straight through).
2. **Direct REST + SSE.** Browser → Next.js proxy → FastAPI. All wire
   shapes match what `src/api/sessions.py`, `src/api/stream.py`,
   `src/api/stop.py`, and `src/api/artifacts.py` already return today.
3. **Reconnect uses `?last_event_id=<seq>` query param**, NOT the
   `Last-Event-ID` HTTP header. The backend's `stream_get` reads from
   the query string (`src/api/stream.py:154`). Each event carries an
   integer `seq` from `ExecutionRegistry.append`; the FE tracks the
   highest seq it has observed and re-passes it on reconnect.
4. **AG-UI event union is the wire vocabulary the FE reducer sees.**
   The backend's `src/streaming/sse.py` validates dict frames against
   `ag_ui.core.events.Event` (the discriminated union) and silently
   drops anything unknown. Therefore the FE never sees runner-internal
   frames like `runner_ready` or `heartbeat`; it only sees AG-UI typed
   events.
5. **Two scoped runner patches land in this milestone** (Stream C,
   paired with Stream B) so the FE consumes proper AG-UI `CUSTOM`
   events for tool denials and artifact attachments rather than
   pattern-matching tool-result strings or refetching session detail.
   See §2.3 (Stream C) and §11 for the exact event names and value
   shapes both ends agree on. The FE depends on these CUSTOM events
   landing.
6. **Recovery window = ExecutionRegistry TTL = 5 minutes after
   `RUN_FINISHED`/`RUN_ERROR`** (see
   `src/streaming/execution_registry.py`). This is NOT the runner
   heartbeat-timeout (~30 s, `runner_heartbeat_timeout_s`), which
   only bounds how long a *live* runner can be silent before it is
   force-killed. Reconnect cursor is `?last_event_id=<seq>` on the
   `GET /v1/sessions/{id}/stream` route.

---

## 1. File layout

```
kloc-agent/frontend/
├── Dockerfile                       # Node 22 alpine, standalone output
├── package.json
├── package-lock.json
├── next.config.ts                   # output: "standalone", reactStrictMode
├── tsconfig.json                    # strict, @/* -> ./src/*
├── eslint.config.mjs
├── postcss.config.mjs               # Tailwind 4 PostCSS plugin
├── .gitignore
├── public/
│   └── favicon.svg
└── src/
    ├── app/
    │   ├── layout.tsx               # <html lang="en">, theme bootstrap, fonts
    │   ├── globals.css              # Tailwind 4 @theme + dark variants
    │   ├── page.tsx                 # Landing surface (UC1, UC2 entry)
    │   ├── s/
    │   │   └── [sessionId]/
    │   │       └── page.tsx         # Conversation surface (UC3, UC4, UC5)
    │   └── api/
    │       └── agent-proxy/
    │           ├── route.ts         # POST → backend /stream (SSE forward)
    │           └── resume/
    │               └── route.ts     # GET → backend /stream (resume forward)
    ├── components/
    │   ├── Shell.tsx                # 2-column flex layout container
    │   ├── Sidebar.tsx              # Session rail (Stream A)
    │   ├── SessionListItem.tsx
    │   ├── NewSessionButton.tsx
    │   ├── ThemeToggle.tsx
    │   ├── RailFooter.tsx
    │   ├── EmptyState.tsx
    │   ├── ErrorBanner.tsx
    │   ├── ConnectionBanner.tsx     # idle | connecting | live | replaying | offline
    │   ├── ConversationHeader.tsx
    │   ├── Thread.tsx               # Renders ordered list of bubbles
    │   ├── AnalystBubble.tsx
    │   ├── AssistantBubble.tsx
    │   ├── ToolCallCard.tsx         # running | done | denied
    │   ├── ArtifactChip.tsx
    │   ├── CodeChip.tsx             # inline `<code>` formatter
    │   ├── InputBar.tsx
    │   └── BlinkingCaret.tsx
    ├── lib/
    │   ├── types.ts                 # FROZEN interface contract (see §3)
    │   ├── api.ts                   # REST client, jsonOrThrow<T>()
    │   ├── agui.ts                  # AG-UI client wrapper + SSE consumer
    │   ├── reducer.ts               # Event reducer (see §4)
    │   ├── runLoop.ts               # Submit + stream + reconnect orchestration
    │   ├── theme.ts                 # Theme bootstrap + persistence
    │   ├── time.ts                  # "14:32" / "Sat" relative formatter
    │   └── cn.ts                    # className helper
    └── styles/
        └── fonts.ts                 # next/font imports for Geist + JetBrains Mono
```

Notes:
- All UI components are server components by default; only the ones
  that hold state (`Sidebar`, `Thread`, `InputBar`, `ConnectionBanner`,
  `ThemeToggle`, the page-level Conversation container) are marked
  `"use client"`.
- The Next.js `app/api/agent-proxy/route.ts` handler is a thin SSE
  forward; it does NOT translate AG-UI envelopes or call `HttpAgent`
  server-side. We keep it because (a) the browser doesn't need CORS to
  the FastAPI backend, (b) we can attach a request ID / OTEL trace on
  the server boundary, (c) it matches the existing Docker network
  topology (browser ↔ Next.js ↔ backend) the compose file already
  expects. Runtime: Node (not Edge) so we can stream `Response.body`
  unbuffered.

---

## 2. Three parallel work streams

The streams are split so that **no file is owned by more than one
developer**. Stream A owns `lib/types.ts` and writes it FIRST so
Stream B can import the frozen contract from day one. Stream C is a
small backend (runner-side) patch pair paired with Stream B because
its wire contract is exactly what Stream B's reducer consumes;
developer-2 owns both Stream B and Stream C to keep the contract
co-owned by one head.

### 2.1 Stream A — Shell + REST + Theme (developer-1)

**Files developer-1 owns and edits:**
- `Dockerfile`
- `package.json`, `package-lock.json` (initial scaffold)
- `next.config.ts`, `tsconfig.json`, `eslint.config.mjs`,
  `postcss.config.mjs`
- `public/favicon.svg`
- `src/app/layout.tsx`
- `src/app/globals.css`
- `src/app/page.tsx`
- `src/app/s/[sessionId]/page.tsx` (shell only; thread region is a
  Stream B component imported by reference)
- `src/components/Shell.tsx`
- `src/components/Sidebar.tsx`
- `src/components/SessionListItem.tsx`
- `src/components/NewSessionButton.tsx`
- `src/components/ThemeToggle.tsx`
- `src/components/RailFooter.tsx`
- `src/components/EmptyState.tsx`
- `src/components/ErrorBanner.tsx`
- `src/components/ConversationHeader.tsx`
- `src/lib/api.ts`
- `src/lib/types.ts` (frozen at start of Day 2; see §3)
- `src/lib/theme.ts`
- `src/lib/time.ts`
- `src/lib/cn.ts`
- `src/styles/fonts.ts`

**Acceptance coverage:** UC1 (all three scenarios), UC2 (both
scenarios), UC3 (resume-session — Session header rendering, rail
active-state styling, return-to-list state preservation via cache).

### 2.2 Stream B — AG-UI run loop + Thread + Input (developer-2)

**Files developer-2 owns and edits:**
- `src/app/api/agent-proxy/route.ts`
- `src/app/api/agent-proxy/resume/route.ts`
- `src/components/Thread.tsx`
- `src/components/AnalystBubble.tsx`
- `src/components/AssistantBubble.tsx`
- `src/components/ToolCallCard.tsx`
- `src/components/ArtifactChip.tsx`
- `src/components/CodeChip.tsx`
- `src/components/InputBar.tsx`
- `src/components/BlinkingCaret.tsx`
- `src/components/ConnectionBanner.tsx`
- `src/lib/agui.ts`
- `src/lib/reducer.ts`
- `src/lib/runLoop.ts`

**Acceptance coverage:** UC3 (Messages render in order on resume;
oldest first), UC4 (all six scenarios — progressive deltas, tool-call
lifecycle, tool denial, artifact attachment, closed-session input
state, RUN_ERROR retry affordance), UC5 (all three scenarios —
reconnect within window, completed-while-disconnected, recovery
window exceeded).

### 2.3 Stream C — Runner CUSTOM events (developer-2, paired with Stream B)

Two small patches to `runner/hooks/audit.py` (and possibly one helper
in `runner/__main__.py` if needed) so the runner emits AG-UI `CUSTOM`
events the FE reducer can consume directly. The runner already emits
`CUSTOM` events today (e.g. `name="HookBackpressure"`,
`runner/hooks/audit.py:130, :176`) via the same outbound JSONL
channel that carries text deltas — these patches reuse that channel,
no new transport.

**Files developer-2 owns and edits for Stream C:**
- `runner/hooks/audit.py` — emit the two CUSTOM events from inside
  the existing `before_tool_call` (denial path) and the
  `ArtifactRegistered` follow-up.
- `tests/runner/test_hooks_audit.py` — new tests (or extend existing
  if the file already exists in `tests/runner/`). Mirrors the
  established `tests/` layout used by the rest of the backend
  (`test_<module_name>.py`, `asyncio_mode = "auto"` from pyproject).
  Patterns: (a) BeforeToolCall webhook returns `{decision: "deny",
  reason: "deny_list"}` → assert a single CUSTOM frame with
  `name="ToolCallDenied"` is emitted via `_emit_custom_event`;
  (b) artifact-registered round-trip → assert a single CUSTOM frame
  with `name="ArtifactAttached"` is emitted carrying the artifact
  fields the backend returned in `{artifact_id, created: true|false}`.

**Wire contract (FROZEN — both ends must match):**

`name="ToolCallDenied"` value:

```json
{
  "toolCallId": "<string, mirrors TOOL_CALL_START.toolCallId>",
  "toolName":   "<string>",
  "reason":     "<string, e.g. \"deny_list\" / \"policy_deadline_exceeded\" / \"policy_unavailable\">"
}
```

Emission point: inside `AuditHookSender.before_tool_call`
(`runner/hooks/audit.py`) immediately after the webhook responds
with `decision == "deny"` AND immediately after `event.cancel_tool
= response.get("reason", ...)` is set (so the runner is already
committed to cancelling). Also emit when the timeout path sets
`event.cancel_tool = "policy_deadline_exceeded"`, and when the
fail-closed path sets `event.cancel_tool = "policy_unavailable"`.
That gives the FE one consistent signal regardless of which deny
sub-reason fired.

`name="ArtifactAttached"` value:

```json
{
  "artifactId": "<uuid string from webhook response.artifact_id>",
  "filename":   "<string>",
  "sizeBytes":  <integer>,
  "mimeType":   "<string, e.g. \"text/markdown\">"
}
```

Emission point: inside whatever helper the runner uses today to POST
the `ArtifactRegistered` webhook, immediately after a successful
response (status 202 with `{artifact_id, created}`). The runner has
the filename / size / mimeType locally because it produced them; it
takes `artifactId` from the backend response.

Both events use `type: "CUSTOM"` (uppercase) — matching the existing
`HookBackpressure` precedent and the AG-UI 0.1.18 wire enum. The FE
reducer matches on `evt.type === "CUSTOM" && evt.name === "..."`.

**Acceptance coverage:** UC4 #3 (Tool Denial), UC4 #4 (Artifact
attachment). The same UC4 scenarios that Stream B already covers —
Stream C just makes the underlying signal a typed event instead of a
sentinel-string heuristic.

### 2.4 Interface boundary

Stream A exports two things Stream B imports:
- The FROZEN `src/lib/types.ts` (see §3).
- A `<ConversationShell>` boundary in `src/app/s/[sessionId]/page.tsx`
  that provides the header + the input row + scrollable thread
  container, with `children` slot for the thread. Stream B's
  `<Thread>` mounts inside.

Stream B exports two things Stream A imports:
- `<Thread session={...} />` — the message-list region.
- `<InputBar onSubmit={...} disabled={...} />` — used by the
  conversation page.

The page composition (Stream A's `src/app/s/[sessionId]/page.tsx`) is
the only file that imports from both streams; it does NOT touch the
internals of either side. This keeps the touch-set disjoint per dev.

---

## 3. Frozen interface contract — `src/lib/types.ts`

Stream A writes this file FIRST (Day 1, before any UI), commits it,
and treats it as frozen. Stream B imports these types verbatim. Any
change to this file after freeze requires both devs to coordinate.

```ts
// ============================================================================
// REST shapes (mirror `src/api/sessions.py` Pydantic models 1:1)
// ============================================================================

export type RunnerState = "idle" | "spawning" | "running" | "closing";

export interface SessionListItem {
  id: string;                      // uuid
  title: string;
  runner_state: RunnerState;
  message_count: number;
  created_at: string;              // ISO
  updated_at: string;              // ISO
  closed_at: string | null;
}

export interface SessionList {
  sessions: SessionListItem[];
}

export interface SessionDetail {
  id: string;
  analyst_id: string;
  title: string;
  runner_state: RunnerState;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface PersistedMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  content_parts: Record<string, unknown> | null;
  model: string | null;
  seq: number;
  created_at: string;
  finalized_at: string | null;
}

export interface MessagesPage {
  messages: PersistedMessage[];
  next_cursor: number | null;
  has_more: boolean;
}

export interface CreateSessionResponse {
  session_id: string;
  created_at: string;
}

export interface PostMessageResponse {
  run_id: string;
  message_id: string;
  stream_url: string;
}

// ============================================================================
// AG-UI event union (subset the FE actually handles, lifted from
// ag-ui-protocol 0.1.18). Field naming matches the wire (camelCase,
// because `EventEncoder` uses `by_alias=True`).
// ============================================================================

export type AGUIEventType =
  | "RUN_STARTED"
  | "RUN_FINISHED"
  | "RUN_ERROR"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "TEXT_MESSAGE_END"
  | "TOOL_CALL_START"
  | "TOOL_CALL_ARGS"
  | "TOOL_CALL_END"
  | "TOOL_CALL_RESULT"
  | "STATE_SNAPSHOT"
  | "CUSTOM";

export interface BaseEvent<T extends AGUIEventType> {
  type: T;
  timestamp?: number;
}

export interface RunStartedEvent extends BaseEvent<"RUN_STARTED"> {
  threadId: string;
  runId: string;
}
export interface RunFinishedEvent extends BaseEvent<"RUN_FINISHED"> {
  threadId: string;
  runId: string;
}
export interface RunErrorEvent extends BaseEvent<"RUN_ERROR"> {
  threadId: string;
  runId: string;
  code: string;
  message: string;
  cause?: string;
}
export interface TextMessageStartEvent extends BaseEvent<"TEXT_MESSAGE_START"> {
  messageId: string;
  role: "assistant";
}
export interface TextMessageContentEvent extends BaseEvent<"TEXT_MESSAGE_CONTENT"> {
  messageId: string;
  delta: string;
}
export interface TextMessageEndEvent extends BaseEvent<"TEXT_MESSAGE_END"> {
  messageId: string;
}
export interface ToolCallStartEvent extends BaseEvent<"TOOL_CALL_START"> {
  toolCallId: string;
  toolCallName: string;
  parentMessageId?: string;
}
export interface ToolCallArgsEvent extends BaseEvent<"TOOL_CALL_ARGS"> {
  toolCallId: string;
  delta: string;                   // accumulating JSON-encoded args
}
export interface ToolCallEndEvent extends BaseEvent<"TOOL_CALL_END"> {
  toolCallId: string;
}
export interface ToolCallResultEvent extends BaseEvent<"TOOL_CALL_RESULT"> {
  toolCallId: string;
  messageId?: string;
  content: string;                 // tool result body (may be a denial reason)
}
export interface StateSnapshotEvent extends BaseEvent<"STATE_SNAPSHOT"> {
  snapshot: Record<string, unknown>;
}
// CUSTOM events the FE understands. The runner emits these via the
// shared outbound JSONL channel; the backend pipes them through SSE
// untouched. Names + value shapes are frozen — see §2.3 (Stream C)
// and §11 for the runner-side contract.
export interface ToolCallDeniedCustom extends BaseEvent<"CUSTOM"> {
  name: "ToolCallDenied";
  value: { toolCallId: string; toolName: string; reason: string };
}
export interface ArtifactAttachedCustom extends BaseEvent<"CUSTOM"> {
  name: "ArtifactAttached";
  value: {
    artifactId: string;
    filename: string;
    sizeBytes: number;
    mimeType: string;
  };
}
export interface HookBackpressureCustom extends BaseEvent<"CUSTOM"> {
  name: "HookBackpressure";
  value: { event: string; tool?: string; reason: string };
}
// Any other CUSTOM event the runner emits in future. Reducer ignores it.
export interface UnknownCustomEvent extends BaseEvent<"CUSTOM"> {
  name: string;
  value: unknown;
}
export type CustomEvent_ =
  | ToolCallDeniedCustom
  | ArtifactAttachedCustom
  | HookBackpressureCustom
  | UnknownCustomEvent;

export type AGUIEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | RunErrorEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | ToolCallResultEvent
  | StateSnapshotEvent
  | CustomEvent_;

// ============================================================================
// Client-side state models (what the reducer builds and the UI renders)
// ============================================================================

export type ToolCallState = "running" | "done" | "denied";

export interface ToolCallView {
  id: string;                      // toolCallId
  name: string;                    // toolCallName
  args: string;                    // accumulated args (may not be valid JSON yet)
  state: ToolCallState;
  result?: string;                 // body of TOOL_CALL_RESULT, OR denial reason
  meta?: string;                   // short summary shown right-aligned
}

export interface ArtifactView {
  id: string;
  filename: string;
  size_bytes: number;
  content_type: string;
}

export interface MessageView {
  id: string;
  role: MessageRole;
  content: string;
  finalized: boolean;
  toolCalls: ToolCallView[];
  artifacts: ArtifactView[];
  created_at?: string;             // for persisted rows
  seq?: number;
}

export type ConnectionState =
  | "idle"                         // no in-flight run
  | "connecting"                   // POST /stream opened, awaiting RUN_STARTED
  | "live"                         // streaming
  | "replaying"                    // resume in progress (replay from cursor)
  | "offline"                      // network drop detected
  | "error";                       // RUN_ERROR or transport failure

export interface SessionViewState {
  detail: SessionDetail | null;
  messages: MessageView[];
  hasMoreHistory: boolean;
  oldestSeq: number | null;
  connection: ConnectionState;
  activeRunId: string | null;
  lastEventSeq: number | null;     // for last_event_id reconnect cursor
  errorMessage: string | null;
}
```

---

## 4. Event reducer design — `src/lib/reducer.ts`

Pure function `applyEvent(state: SessionViewState, evt: AGUIEvent,
seq: number): SessionViewState`. The seq comes from a monotonic
counter incremented by `lib/runLoop.ts` for every event received from
the stream (live or replayed).

Per-event handling (see also `kloc-analyst.html` for visual targets):

| AG-UI event | Reducer effect |
|---|---|
| `RUN_STARTED` | `connection = "live"`, `activeRunId = runId`, clear `errorMessage` |
| `TEXT_MESSAGE_START` (may be implicit) | Insert / find assistant `MessageView` with `id = messageId`, `finalized = false` |
| `TEXT_MESSAGE_CONTENT` | Append `delta` to that message's `content` (lazy-create the row if `START` was missing — `ag_ui_strands` does not always emit it) |
| `TEXT_MESSAGE_END` | Set `finalized = true` on that message |
| `TOOL_CALL_START` | Append a `ToolCallView { state: "running", name, args: "" }` to the assistant message identified by `parentMessageId` (or to the most recent unfinalized assistant message if absent) |
| `TOOL_CALL_ARGS` | Concat `delta` onto that tool-call's `args` |
| `TOOL_CALL_END` | Transition `state: "done"` (idempotent: leave `"denied"` untouched if a prior `ToolCallDenied` CUSTOM event already set it) |
| `TOOL_CALL_RESULT` | Set `result = content` and compute a short `meta` heuristic ("N results", "ok", "N chars"). Does NOT decide denial — that's the `ToolCallDenied` CUSTOM event's job. |
| `RUN_FINISHED` | `connection = "idle"`, `activeRunId = null`; finalize any still-open assistant message |
| `RUN_ERROR` | `connection = "error"`, `errorMessage = message`, `activeRunId = null`; assistant message stays `finalized = false` (renders with retry affordance) |
| `STATE_SNAPSHOT` | No-op for rendering; ignored by FE |
| `CUSTOM` (`name: "ToolCallDenied"`) | Find the `ToolCallView` matching `value.toolCallId` and set `state: "denied"`, `meta: "DENIED"`, `result: value.reason`. Idempotent — applying the same denial twice on reconnect-replay leaves the view identical. |
| `CUSTOM` (`name: "ArtifactAttached"`) | Append an `ArtifactView` with `{id: value.artifactId, filename, size_bytes: value.sizeBytes, content_type: value.mimeType}` to the most recent assistant `MessageView` (i.e. the one currently being streamed; if none is open yet, attach to the next assistant message that opens). Idempotent on `artifactId`. |
| `CUSTOM` (`name: "HookBackpressure"`) | Surface a transient toast via `ConnectionBanner` (no state mutation beyond that) |
| `CUSTOM` (other names) | No-op; logged at INFO via console.debug |

`lastEventSeq` is set to `seq` on every call regardless of type — this
is the reconnect cursor.

The reducer is pure and synchronous. The `runLoop.ts` orchestrator
owns the actual `useReducer` (or `useSyncExternalStore`-style)
binding; the reducer file contains only logic.

---

## 5. Run loop + reconnect — `src/lib/runLoop.ts` + `src/lib/agui.ts`

### Submit path (UC4)

1. User types and presses ⌘↵ / Enter / clicks send.
2. `runLoop.submit(text)` optimistically appends an analyst
   `MessageView` to local state with `id = crypto.randomUUID()` and
   `finalized: true`. (Backend persistence happens server-side inside
   `POST /stream`; if it fails the optimistic bubble is rolled back.)
3. Compute `run_id = crypto.randomUUID()`.
4. `agui.openRunStream({ sessionId, runId, messages })`:
   - Calls `fetch("/api/agent-proxy", { method: "POST", body:
     JSON.stringify({ runId, sessionId, messages }) })`.
   - The proxy POSTs to `${BACKEND_URL}/v1/sessions/{sessionId}/stream`
     with the same JSON body, forwarding `Accept: text/event-stream`,
     and streams the response body back unbuffered.
   - On the client side, the response body is parsed as an SSE stream
     using `@ag-ui/client 0.0.42`'s `HttpAgent` event-source-like
     reader OR a thin hand-rolled SSE parser (whichever is exposed by
     the package — Stream B verifies on Day 1 and picks one). Each
     parsed event is yielded as an `AGUIEvent`.
5. Each event flows through the reducer; React re-renders.
6. The loop terminates on `RUN_FINISHED` or `RUN_ERROR`.

### Reconnect path (UC5)

A `useEffect` cleanup detects an unexpected stream close (no
`RUN_FINISHED` / `RUN_ERROR` observed and `connection === "live"`):

1. Set `connection = "offline"`.
2. Wait for `navigator.onLine === true` (use `online` event or
   exponential backoff up to 5 s).
3. Call `fetch("/api/agent-proxy/resume?session_id=...&run_id=...&last_event_id=<seq>")`.
4. The proxy GETs `${BACKEND_URL}/v1/sessions/{sessionId}/stream?run_id=<rid>&last_event_id=<seq>`.
5. Set `connection = "replaying"` until the first event newer than
   `lastEventSeq` arrives, then `connection = "live"`.
6. Reducer applies replayed events idempotently (it already does —
   the seq is monotonic and `applyEvent` is pure).
7. On the proxy receiving HTTP 404 (recovery-window-exceeded — the
   ExecutionRegistry has dropped the run), fall back to UC5's third
   scenario: refetch `GET /v1/sessions/{id}/messages?limit=500` and
   replace the in-memory thread with the persisted Messages. Set
   `connection = "idle"`.

`ConnectionBanner` reflects `connection` directly:

| State | Banner |
|---|---|
| `idle` | hidden |
| `connecting` | "connecting…" with neutral spinner |
| `live` | hidden (status shown by the blinking caret in the active bubble) |
| `replaying` | "catching up…" with mono cursor-style hint |
| `offline` | warning band: "reconnecting…" |
| `error` | danger band: error message + "retry" button (re-issues the last user message) |

---

## 6. REST client — `src/lib/api.ts` (Stream A)

```ts
const BROWSER_BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

async function jsonOrThrow<T>(res: Response): Promise<T> { /* ... */ }

export async function listSessions(opts?: { includeClosed?: boolean }): Promise<SessionList> { /* GET /v1/sessions */ }
export async function createSession(body?: { title?: string }): Promise<CreateSessionResponse> { /* POST /v1/sessions */ }
export async function getSession(id: string): Promise<SessionDetail> { /* GET /v1/sessions/{id} */ }
export async function listMessages(id: string, params?: { after?: number; limit?: number }): Promise<MessagesPage> { /* GET /v1/sessions/{id}/messages */ }
export async function closeSession(id: string): Promise<void> { /* POST /v1/sessions/{id}/close */ }
export async function cancelRun(sessionId: string, runId: string): Promise<void> { /* POST /v1/sessions/{id}/runs/{run_id}/cancel */ }
export function artifactDownloadUrl(artifactId: string): string { return `${BROWSER_BACKEND_URL}/v1/artifacts/${artifactId}`; }
```

All calls go directly browser → backend (CORS allowed). The
`agent-proxy` routes are only for SSE because we want server-side
streaming control and a single network egress shape for the
streaming case.

---

## 7. Per-component coverage of behavior.xml

| Component | Behavior.xml use-case / rule / NFR | Spec scenario |
|---|---|---|
| `Sidebar` + `SessionListItem` | `beh.browse-sessions`, rule `beh.rule.single-analyst-ownership` | UC1 #1 (3 sessions newest first, grouped) |
| `EmptyState` | `beh.browse-sessions` alt: no-prior-sessions | UC1 #2 |
| `ErrorBanner` (rail) | `beh.browse-sessions` alt: retrieval-failed | UC1 #3 |
| `NewSessionButton` | `beh.start-new-session` | UC2 #1, #2 |
| Conversation page (`/s/[sessionId]`) | `beh.resume-session`, rule `beh.rule.prior-history-shown-on-resume`, invariant `inv.message-sequence-monotonic`, NFR `nfr.history-page-size` | UC3 #1, #2, #3 |
| `ConversationHeader` | (presentation; back-affordance enables UC3 #3) | UC3 #3 |
| `Thread` + `AnalystBubble` + `AssistantBubble` | `beh.ask-assistant`, rules `beh.rule.message-order`, `beh.rule.analyst-message-persisted-first`, `beh.rule.progressive-streaming` | UC4 #1 |
| `ToolCallCard` (state="running"/"done") | rule `beh.rule.tool-call-visible` | UC4 #2 |
| `ToolCallCard` (state="denied") | rule `beh.rule.tool-denial-distinct`, invariant `inv.tool-denial-distinguishable`, NFR `nfr.tool-denial-affordance` | UC4 #3 |
| `ArtifactChip` | rule `beh.rule.artifact-surfaced`, invariant `inv.artifact-named` | UC4 #4 |
| `InputBar` (disabled when `closed_at != null`) | rule `beh.rule.closed-session-immutable`, invariant `inv.no-message-after-close` | UC4 #5 |
| `AssistantBubble` (unfinalized + retry) + `ConnectionBanner` (error) | `beh.ask-assistant` alt: assistant-failed | UC4 #6 |
| `runLoop.ts` resume + `ConnectionBanner` ("replaying") | `beh.recover-from-disconnect`, rule `beh.rule.resume-replays-cursor`, NFR `nfr.disconnect-recovery` | UC5 #1 |
| Resume-after-complete path | UC5 alt: reply-already-completed | UC5 #2 |
| Resume-404 fallback to `/messages` | UC5 alt: recovery-window-exceeded | UC5 #3 |
| `layout.tsx` setting `<html lang="en">` | NFR `nfr.document-language` | (footnote NFR) |
| `BlinkingCaret` inside in-flight `AssistantBubble` | NFR `nfr.progressive-rendering` | UC4 #1 |

---

## 8. Visual-token mapping

CSS custom properties live in `src/app/globals.css` under Tailwind 4's
`@theme` block. The dark theme uses CSS variable overrides scoped to
`[data-theme="dark"]`. `theme.ts` writes `data-theme` to
`document.documentElement` based on `prefers-color-scheme` and a
`localStorage["kloc-theme"]` override.

| Mockup variable | Tailwind 4 `@theme` token | Light value | Dark value |
|---|---|---|---|
| `--color-ink` | `--color-ink` | `#0c0a09` | `#f5f5f4` |
| `--color-ink-muted` | `--color-ink-muted` | `#57534d` | `#a8a29e` |
| `--color-ink-faint` | `--color-ink-faint` | `#a8a29e` | `#78716c` |
| `--color-line` | `--color-line` | `#e7e5e3` | `#27272a` |
| `--color-line-strong` | `--color-line-strong` | `#d6d3d0` | `#3f3f46` |
| `--color-canvas` | `--color-canvas` | `#ffffff` | `#1c1917` |
| `--color-canvas-rail` | `--color-canvas-rail` | `#faf9f7` | `#18181b` |
| `--color-canvas-sunk` | `--color-canvas-sunk` | `#f5f4f1` | `#27272a` |
| `--color-accent` | `--color-accent` | `#2b5cff` | `#7c92ff` |
| `--color-success` | `--color-success` | `#15803d` | `#22c55e` |
| `--color-warning` | `--color-warning` | `#b45309` | `#f59e0b` |
| `--color-danger-bg` | `--color-danger-bg` | `#fef2f2` | `#3f1d1d` |
| `--color-danger-line` | `--color-danger-line` | `#fecaca` | `#7f1d1d` |
| `--color-danger-ink` | `--color-danger-ink` | `#991b1b` | `#fca5a5` |
| `--font-sans` | `--font-sans` | `var(--font-geist)` | (shared) |
| `--font-mono` | `--font-mono` | `var(--font-jetbrains)` | (shared) |

The token NAMES are identical to the mockup so a reviewer can grep
the repo against `kloc-analyst.html`.

---

## 9. Dev-server + Docker commands

For local dev (developer workflow):

```bash
# Backend (in another terminal)
cd kloc-agent
make up                       # docker-compose stack: postgres, minio, backend

# Frontend
cd kloc-agent/frontend
npm install
npm run dev                   # next dev on http://localhost:3000
```

For Docker build:

```bash
cd kloc-agent
docker compose build frontend
docker compose up frontend
```

`docker-compose.yml` already declares the `frontend` service pointing
at `./frontend/Dockerfile` and exposing `:3000` — Stream A's job is
to make those references resolve. The build args
`NEXT_PUBLIC_BACKEND_URL` and `NEXT_PUBLIC_COPILOTKIT_AGENT_NAME` are
already wired; we keep the first and IGNORE the second (CopilotKit is
dropped). Stream A does NOT modify `docker-compose.yml` (the lead may
clean up the dead env var later).

`package.json` scripts (initial set):

```json
{
  "dev": "next dev",
  "build": "next build",
  "start": "next start",
  "lint": "next lint",
  "typecheck": "tsc --noEmit"
}
```

QA validation commands:

```bash
cd kloc-agent/frontend
npm run typecheck             # must pass
npm run lint                  # must pass
npm run build                 # must succeed; produces .next/standalone
```

Interactive QA: see PM spec §"Browser QA Requirement" — Chrome
automation against `http://localhost:3000`.

---

## 10. Sequenced execution order

Day 0 (lead): create `frontend/` directory with `.gitignore` and
empty `package.json` scaffold so both devs can branch off without
clobbering each other.

Day 1:
- Stream A: scaffold Next.js 16.0.8 + React 19.2.1 + TS 5.6 strict +
  Tailwind 4 + `@ag-ui/client 0.0.42`. Write `tsconfig.json`,
  `next.config.ts`, `eslint.config.mjs`, `package.json`. Write
  `src/lib/types.ts` and commit. Notify Stream B that types are
  frozen.
- Stream B: read `src/lib/types.ts`. Scaffold `src/app/api/agent-proxy/route.ts`
  with a minimal POST→backend SSE forward (no UI yet, smoke-tested
  with `curl`).
- Stream C: open `runner/hooks/audit.py`; sketch the two CUSTOM-event
  emission points so the wire shape can be verified end-to-end early
  (before the UI is rich enough to render them). Smoke test by
  hitting a deny-listed tool and inspecting the SSE frames flowing
  through `/v1/sessions/{id}/stream`.

Day 2–4: Stream A and Stream B work in parallel on their respective
file sets. Daily sync to confirm no boundary drift. Stream C lands
its two patches + `tests/runner/test_hooks_audit.py` updates inside
this window — its acceptance is that the two CUSTOM events appear on
the wire in the right order with the right value shape; reviewer
checks against §2.3 verbatim.

Day 5: integration on `src/app/s/[sessionId]/page.tsx` — Stream A's
shell mounts Stream B's `<Thread>` and `<InputBar>`. Stream B's
reducer is wired to the real CUSTOM events emitted by Stream C, no
sentinel-string fallback path remains. Both devs present.

Day 6: theme polish; QA hand-off.

---

## 11. Runner CUSTOM events (Stream C deliverables) + transport notes

These were originally architect-flagged gaps (Phase 1). Lead approved
two scoped runner patches; they are now part of the milestone as
Stream C. The wire shapes below are the FROZEN contract — Stream B's
reducer (§4) and `lib/types.ts` (§3) consume them verbatim, and
Stream C's tests in `tests/runner/test_hooks_audit.py` assert them
verbatim.

### 11.1 `ToolCallDenied` CUSTOM event

`ag-ui-protocol 0.1.18` defines no `TOOL_CALL_DENIED` enum value, so
denial is conveyed as a `CUSTOM` AG-UI event. The runner's
`AuditHookSender.before_tool_call` emits this event on the same
JSONL channel the existing `HookBackpressure` CUSTOM event already
uses (`runner/hooks/audit.py:130`, `:176`).

```json
{
  "type": "CUSTOM",
  "name": "ToolCallDenied",
  "value": {
    "toolCallId": "<mirrors TOOL_CALL_START.toolCallId>",
    "toolName":   "<string>",
    "reason":     "<deny_list | policy_deadline_exceeded | policy_unavailable | ...>"
  }
}
```

Emission MUST happen in three places inside `before_tool_call`:
- when the webhook returns `decision == "deny"` and the handler sets
  `event.cancel_tool = response.get("reason", "policy_denied")`;
- when the 2-second deadline lapses and the handler sets
  `event.cancel_tool = "policy_deadline_exceeded"`;
- when an unexpected error fails closed with
  `event.cancel_tool = "policy_unavailable"`.

The denial signal is emitted exactly once per `toolCallId`. The FE
reducer is idempotent (applying the same denial twice on
reconnect-replay is a no-op).

### 11.2 `ArtifactAttached` CUSTOM event

Artifacts today are persisted server-side via the HMAC webhook path
(`tool_call.* → artifact_registered` audit row) but never reach the
SSE stream. Stream C emits a `CUSTOM` event from the runner
immediately after a successful `ArtifactRegistered` webhook
round-trip (which returns `{artifact_id, created}`).

```json
{
  "type": "CUSTOM",
  "name": "ArtifactAttached",
  "value": {
    "artifactId": "<uuid from webhook response>",
    "filename":   "<string>",
    "sizeBytes":  <int>,
    "mimeType":   "<string>"
  }
}
```

The reducer attaches the artifact to the most recent assistant
`MessageView` (the one currently being streamed). The `ArtifactChip`
component clicks through to `GET /v1/artifacts/{artifactId}` which
the backend 302s to a presigned MinIO URL (`src/api/artifacts.py`).

### 11.3 `last_event_id` is a query param, not a header

The backend reads the reconnect cursor from `?last_event_id=<seq>`
(see `src/api/stream.py:154`, `Query(None, alias="last_event_id")`).
The browser's native `EventSource` only sends `Last-Event-ID` as an
HTTP header on auto-reconnect. We therefore cannot use plain
`EventSource` for resume — we use `fetch()` + a stream reader (or
`@ag-ui/client`'s `HttpAgent`) so we can pass the cursor in the
query string under our control.

---

## 12. Risk register

| Risk | Mitigation |
|---|---|
| `@ag-ui/client 0.0.42` API surface differs from CopilotKit-era assumptions | Stream B verifies on Day 1 with a smoke test against the real backend; if the package doesn't expose a usable SSE reader, hand-roll one (~30 lines) using `Response.body!.getReader()` and the standard SSE framing rules |
| Two tabs of the same session diverge | Out of scope (PM spec §"Out of Scope"). M0 accepts divergence until next reconnect |
| `RUN_FINISHED` arrives before the `_persist_events` task has committed all deltas | Reducer handles this; backend already does too (`is_run_lifecycle_terminal` ends the consumer loop). FE renders whatever has arrived; persistence catches up server-side |
| Theme flash on first paint | `layout.tsx` reads the persisted theme on the server side from a cookie if we choose to set one; otherwise a tiny `<script>` in `<head>` syncs `data-theme` before React hydrates |
| Stream C runner patch slips past Day 4 | Stream B can't render denial or artifact UI without it. Mitigation: Stream B implements `ToolCallCard` / `ArtifactChip` against the typed CUSTOM events from Day 1 using stubbed wire frames (handwritten JSON in a dev-only injection helper); when Stream C lands, the stub is deleted. Hard blocker for QA sign-off, not for the rest of Stream B's progress. |
| Stream C event name drift between FE and runner | The CUSTOM event names + value shapes are pinned in §2.3 + §11 + `lib/types.ts`. `tests/runner/test_hooks_audit.py` asserts the runner side; the FE reducer's union discriminates on string literals, so a name typo is a TypeScript error. |

---

## 13. Done-criteria for the plan

This plan is "done" (i.e. ready for developer hand-off) when:

- [x] Three parallel streams identified with disjoint file ownership
      (Stream A FE shell, Stream B FE run-loop, Stream C runner
      CUSTOM events).
- [x] Frozen interface contract (`lib/types.ts`) written, including
      REST shapes, AG-UI event union, the two CUSTOM event payloads
      (`ToolCallDenied`, `ArtifactAttached`), and client-side view
      models.
- [x] Per-component mapping to behavior.xml use-cases / rules /
      invariants / NFRs.
- [x] Reconnect-replay design documented (cursor via query param,
      recovery window = 5 min ExecutionRegistry TTL post
      `RUN_FINISHED`, proxy route, 404 fallback to `/messages`).
- [x] Visual-token mapping table (light + dark) matching PM spec.
- [x] Dev-server and Docker commands defined.
- [x] Stream C runner patches scoped: exact emission points in
      `runner/hooks/audit.py`, exact CUSTOM event value shapes, test
      file location `tests/runner/test_hooks_audit.py`.

When the developers start, they own:
- developer-1 → Stream A file set.
- developer-2 → Stream B file set + Stream C runner patches.
- Both → the integration in `src/app/s/[sessionId]/page.tsx` on Day 5.

The reviewer reviews against this plan plus the PM spec; QA exercises
the Gherkin scenarios per PM spec §"Browser QA Requirement".
