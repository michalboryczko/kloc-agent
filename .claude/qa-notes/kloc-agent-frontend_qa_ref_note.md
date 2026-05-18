# QA Reference — kloc-agent frontend (rewrite)

> **Validation method**: interactive Chrome browser automation via
> `mcp__claude-in-chrome__*` tools. Static code review is **not**
> sufficient — every Gherkin scenario in `docs/specs/kloc-agent-frontend.md`
> must be exercised against the running app at `http://localhost:3000`
> with the backend at `http://localhost:8000`.
>
> Authoritative sources:
> - `docs/behavior.xml` — UC1..UC5, rules, invariants, NFRs
> - `docs/specs/kloc-agent-frontend.md` — feature spec + Gherkin ACs
> - `docs/specs/ui/kloc-analyst.html` — light theme visual contract
> - `docs/specs/ui/img_1.png` — dark theme reference

---

## 0. Pre-flight

### 0.1 Backend stack

The frontend will not function without the full FastAPI backend +
Postgres + MinIO + kloc-intelligence MCP server.

```bash
# 1. Bring up the kloc-intelligence MCP stack (separate compose project)
cd /Users/michal/dev/ai/kloc/kloc-intelligence
docker compose up -d   # Neo4j + Qdrant + mcp-server-http

# 2. Bring up the kloc-agent backend stack
cd /Users/michal/dev/ai/kloc/kloc-agent
cp .env.example .env       # if not already present
# Verify the following are set in .env:
#   LLM_PROVIDER=gemini
#   GEMINI_API_KEY=<operator key from environment>
#   KLOC_RUNNER_MODE=docker
#   KLOC_MCP_URL=http://host.docker.internal:<mcp-port>/mcp
#   KLOC_CORS_ALLOW_ORIGINS=http://localhost:3000
#   DATABASE_URL=postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent
#   MINIO_ENDPOINT_URL=http://localhost:9000
#   ARTIFACT_BUCKET=artifacts
docker compose -f docker-compose.yml up -d postgres minio
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000

# 3. Confirm backend liveness
curl -sf http://localhost:8000/health    # → {"status":"ok"}
curl -sf http://localhost:8000/v1/sessions  # → array (probably empty)
```

### 0.2 Seed data

`beh.browse-sessions` and `beh.resume-session` need pre-existing data
for the non-empty paths. Use `curl` to seed; do not depend on the UI to
create them.

```bash
BACKEND=http://localhost:8000

# A. Empty-state check — leave the DB empty for the first run of UC1.

# B. Seed three Sessions for "Analyst with prior Sessions" scenario.
for title in "Audit auth flow" "Refactor queue jobs" "Find booking race"; do
  curl -sX POST "$BACKEND/v1/sessions" \
    -H 'content-type: application/json' \
    -d "{\"title\":\"$title\"}"
done

# C. Seed a Session with five Messages for `beh.resume-session` happy path.
SID=$(curl -sX POST "$BACKEND/v1/sessions" \
  -H 'content-type: application/json' \
  -d '{"title":"Resume target"}' | jq -r .session_id)
for i in 1 2 3 4 5; do
  curl -sX POST "$BACKEND/v1/sessions/$SID/messages" \
    -H 'content-type: application/json' \
    -d "{\"role\":\"user\",\"content\":\"seeded msg $i\"}"
done
echo "RESUME_SID=$SID"

# D. (Optional) Seed > 500 messages for the pagination NFR. Skip if heavy.
```

### 0.3 Tool-denial harness

The PM spec requires a tool-denial card in UC4. Configure the backend
to deny a known tool before exercising that scenario:

```bash
# Set KLOC_DENY_TOOLS to a tool the assistant routinely calls; .env
# triggers it reliably for the JWT-secret prompt.
KLOC_DENY_TOOLS=read_file uv run uvicorn src.main:app --port 8000
```

**Wire-level shape (per team-lead amendment)**: denial is delivered to
the browser as an AG-UI **CUSTOM event**, not a reserved event:

```
event: CUSTOM
data: {"name":"ToolCallDenied","value":{"toolCallId":"...","reason":"..."}}
```

QA inspects the SSE frame contents via
`mcp__claude-in-chrome__read_network_requests` looking for a CUSTOM
event with `name === "ToolCallDenied"` and the toolCallId of the
denied call. The DOM-level assertions (4.3 below) cover the rendered
card; this paragraph documents the wire shape so failures can be
distinguished between "backend never emitted the denial" and "frontend
emitted but did not render".

Provoke the denial by asking the assistant to read `.env` after seeding
`KLOC_DENY_TOOLS=read_file`. If the runner does not route to
`read_file`, fall back to a different tool listed in the deny list.

### 0.4 Frontend dev server

```bash
cd /Users/michal/dev/ai/kloc/kloc-agent/frontend
# Confirm Node 22 is on PATH: node -v  # → v22.x
cat > .env.local <<'EOF'
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000
BACKEND_URL=http://localhost:8000
NEXT_TELEMETRY_DISABLED=1
EOF
npm install
npm run dev   # → ready on http://localhost:3000
```

### 0.5 CopilotKit veto

Before any browser work, fail fast if CopilotKit slipped back in:

```bash
cd /Users/michal/dev/ai/kloc/kloc-agent/frontend
grep -E '"@copilotkit/|"copilotkit"' package.json && echo "FAIL: CopilotKit detected" || echo "OK"
```

Treat any match as an automatic FAIL of the validation phase.

---

## 1. UC1 — `beh.browse-sessions`

### 1.1 Browser test plan

```text
Tool: mcp__claude-in-chrome__navigate
  url: http://localhost:3000

Tool: mcp__claude-in-chrome__get_page_text
  → expect: "kloc" wordmark, "New session" button, and a Today/Earlier
    group header.

Tool: mcp__claude-in-chrome__javascript_tool
  expression:
    JSON.stringify({
      items: [...document.querySelectorAll('[data-test="session-item"]')]
        .map(el => ({
          title: el.querySelector('[data-test="session-title"]')?.textContent,
          id:    el.querySelector('[data-test="session-id"]')?.textContent,
          count: el.querySelector('[data-test="session-count"]')?.textContent,
          when:  el.querySelector('[data-test="session-when"]')?.textContent,
        })),
      headers: [...document.querySelectorAll('[data-test="group-header"]')]
        .map(el => el.textContent),
    })

PASS criteria:
  - items.length === 3 (matches seed B)
  - items ordered newest-first (verify `when` against `created_at` from
    POST /v1/sessions responses recorded in 0.2)
  - each item has all four fields populated
  - headers includes "TODAY" (uppercase, mono — verify via computed style)
```

### 1.2 Empty-state path (`alternative-flow: no-prior-sessions`)

```text
Pre-step: wipe sessions via DELETE-the-DB OR run against a fresh DB.
          docker compose exec postgres psql -U kloc -d kloc_agent \
            -c "TRUNCATE sessions, messages, audit_log RESTART IDENTITY CASCADE;"

Tool: mcp__claude-in-chrome__navigate → http://localhost:3000
Tool: mcp__claude-in-chrome__get_page_text

PASS criteria:
  - empty-state copy visible (verbatim phrasing from spec is permitted
    to vary; QA accepts any clear "no sessions yet" indication)
  - "New session" button still rendered and not aria-disabled.
```

### 1.3 Retrieval-failed path (`alternative-flow: retrieval-failed`)

```text
Pre-step: stop uvicorn (Ctrl+C) so GET /v1/sessions yields a network
          error / 5xx.
Tool: mcp__claude-in-chrome__navigate → http://localhost:3000
Tool: mcp__claude-in-chrome__get_page_text
Tool: mcp__claude-in-chrome__read_console_messages → no uncaught errors

PASS criteria:
  - Inline error indication is visible (text mentioning "cannot load"
    / "failed to fetch" or similar).
  - "New session" button remains visible and enabled.
Restart backend before continuing.
```

---

## 2. UC2 — `beh.start-new-session`

### 2.1 Happy path

```text
Tool: mcp__claude-in-chrome__navigate → http://localhost:3000
Tool: mcp__claude-in-chrome__find
  text: "New session"
Tool: mcp__claude-in-chrome__form_input  (or click)
  → click "New session"

Tool: mcp__claude-in-chrome__javascript_tool
  expression: location.pathname

PASS criteria:
  - location.pathname matches /^\/s\/[0-9a-f-]{8,}/
  - thread region empty (no message bubbles)
  - input element is document.activeElement
  - the new Session appears at top of the rail (verify via the same
    JS query as 1.1)

Tool: mcp__claude-in-chrome__read_network_requests
  → expect a POST /v1/sessions with 201 status in the request log.
```

### 2.2 Creation-failure path

```text
Pre-step: block POST /v1/sessions via JS hook (override fetch) OR stop
          the backend.
Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    const orig = window.fetch;
    window.fetch = (url, init) => {
      if (typeof url === 'string' && url.endsWith('/v1/sessions')
          && (init?.method ?? 'GET') === 'POST') {
        return Promise.resolve(new Response('', { status: 500 }));
      }
      return orig(url, init);
    };
Tool: click "New session" via find + form_input.

PASS criteria:
  - location.pathname still "/" (no navigation)
  - visible error indication present (search text via get_page_text)
```

---

## 3. UC3 — `beh.resume-session`

### 3.1 Happy path (five prior messages)

```text
Tool: mcp__claude-in-chrome__navigate → http://localhost:3000

Tool: mcp__claude-in-chrome__find
  text: "Resume target"     (the session title seeded in 0.2 step C)

Tool: mcp__claude-in-chrome__form_input → click that rail entry

Tool: mcp__claude-in-chrome__javascript_tool
  expression:
    JSON.stringify({
      path: location.pathname,
      header_title: document.querySelector('[data-test="thread-title"]')?.textContent,
      header_id:    document.querySelector('[data-test="thread-id"]')?.textContent,
      msgs: [...document.querySelectorAll('[data-test="message"]')]
              .map(el => ({
                role: el.dataset.role,
                seq: Number(el.dataset.seq),
                text: el.textContent.trim().slice(0, 60),
              })),
      active_in_rail: document.querySelector('[data-test="session-item"][data-active="true"]')?.textContent,
    })

PASS criteria:
  - path startsWith "/s/" + RESUME_SID
  - msgs.length === 5
  - msgs sorted by seq ascending (oldest first)
  - the rail entry for "Resume target" has data-active="true"
  - active rail entry has the white-surface + ring styling (verify
    via getComputedStyle on backgroundColor and boxShadow tokens —
    background should resolve to var(--color-canvas) or #fff, and
    the ring colour must be --color-line-strong)
```

### 3.2 Large-history page-size NFR

Per team-lead amendment: you do NOT need to seed 500+ messages.
Verify the request shape and the load-on-demand banner instead.

```text
Approach A — verify request shape:
  Pre-step: open any seeded session via the rail.
  Tool: mcp__claude-in-chrome__read_network_requests
    → expect at least one GET /v1/sessions/<id>/messages with the
      query parameter `limit=500` (exact value).

Approach B — verify the "older messages exist" indication:
  Pre-step: mock the backend's has_more flag. Two options:
    (i) Inject a fetch shim before navigating:
        window.fetch = (u, i) => {
          if (typeof u === 'string' && /\/messages/.test(u)) {
            return Promise.resolve(new Response(JSON.stringify({
              messages: [], has_more: true, next_after: 0
            }), { headers: { 'content-type': 'application/json' } }));
          }
          return origFetch(u, i);
        };
    (ii) Or seed a session with > 500 messages (heavy — only if (i)
         is not practical).

  Tool: mcp__claude-in-chrome__javascript_tool
    expression: |
      document.querySelector('[data-test="older-messages-banner"]')?.textContent
  PASS: banner is rendered and non-empty (load-on-demand affordance,
        NOT infinite scroll).
```

### 3.3 Round-trip back to landing

```text
Tool: navigate back to / (back button OR mcp__claude-in-chrome__navigate)
Tool: re-click the resumed session
Tool: read_network_requests → expect NO refetch of /messages for that
  session-id in the second visit (in-memory cache preserves state).
PASS: 0 new GET /messages requests after first visit.
```

---

## 4. UC4 — `beh.ask-assistant`

### 4.1 Progressive streaming

```text
Pre-step: start a fresh new session (UC2). Backend tool-denial harness
          (0.3) does NOT need to be active for this scenario.

Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    window.__t0 = performance.now();
    const input = document.querySelector('[data-test="message-input"]');
    input.focus();
    // Use the native setter so React's controlled-input syncs.
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
      .call(input, "Where is the JWT secret loaded, and is it ever logged?");
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('[data-test="message-send"]').click();
    "submitted";

Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    new Promise(resolve => {
      const tgt = document.querySelector('[data-test="assistant-bubble"]');
      if (tgt && tgt.textContent.trim().length > 0) {
        resolve({ msSinceSubmit: performance.now() - window.__t0,
                  firstChunk: tgt.textContent.slice(0,40) });
        return;
      }
      const obs = new MutationObserver(() => {
        const t = document.querySelector('[data-test="assistant-bubble"]');
        if (t && t.textContent.trim().length > 0) {
          obs.disconnect();
          resolve({ msSinceSubmit: performance.now() - window.__t0,
                    firstChunk: t.textContent.slice(0,40) });
        }
      });
      obs.observe(document.body, { subtree: true, childList: true, characterData: true });
      setTimeout(() => { obs.disconnect(); resolve({ timeout: true }); }, 5000);
    });

PASS criteria:
  - msSinceSubmit < 1000  (NFR nfr.progressive-rendering, single-shot
    proxy for p95 — flag in report that this is one observation, not
    a true p95 sample)
  - The analyst's submitted Message bubble is visible immediately
    (verify via querySelector before the assistant bubble fills).
  - After RUN_FINISHED the blinking caret element no longer exists
    inside the assistant bubble (`document.querySelector('.blink')`
    should be null or removed from the active bubble).

Tool: mcp__claude-in-chrome__read_network_requests
  → expect a POST /v1/sessions/<sid>/stream with
    Accept: text/event-stream in the request headers.
```

### 4.2 Tool-call lifecycle

**Visible states (per team-lead amendment): exactly three —
`running`, `done`, `denied`.** behavior.xml's "in-progress / executing"
both normalize to `running` in the UI; QA verifies the three-state
vocabulary and FAILS on any other `data-state` value.

```text
Tool: during streaming, mcp__claude-in-chrome__javascript_tool
  expression: |
    [...document.querySelectorAll('[data-test="tool-call"]')]
      .map(el => ({
        state: el.dataset.state,
        name: el.querySelector('[data-test="tool-name"]')?.textContent,
        args: el.querySelector('[data-test="tool-args"]')?.textContent,
        meta: el.querySelector('[data-test="tool-meta"]')?.textContent,
        iconClass: el.querySelector('svg')?.getAttribute('class') ?? '',
      }))

PASS criteria over time:
  - All observed `state` values are one of: "running" | "done" | "denied".
    Any other value FAILS the AC.
  - At least one card observed with state "running" with the spin
    class on its SVG.
  - After TOOL_CALL_END that card transitions to state "done" with a
    check-icon variant (no `spin` class) and a non-empty meta.
  - Card uses --color-line border + white surface (verify via
    getComputedStyle backgroundColor === rgb(255,255,255) in light theme).
```

### 4.3 Tool-denial (`alternative-flow: tool-denied`)

```text
Pre-req: backend started with KLOC_DENY_TOOLS=read_file (see 0.3).
Pre-step: send a question that will provoke a read_file call, e.g.
          "Read JwtFactory.php and tell me where the JWT secret comes from."

Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    const denied = [...document.querySelectorAll('[data-test="tool-call"]')]
      .filter(el => el.dataset.state === 'denied')
      .map(el => {
        const cs = getComputedStyle(el);
        return {
          name: el.querySelector('[data-test="tool-name"]')?.textContent,
          label: el.querySelector('[data-test="denied-label"]')?.textContent,
          background: cs.backgroundColor,
          borderColor: cs.borderColor,
          reason: el.querySelector('[data-test="tool-meta"]')?.textContent,
        };
      });
    JSON.stringify(denied);

PASS criteria (`nfr.tool-denial-affordance`, `inv.tool-denial-distinguishable`):
  - denied.length >= 1
  - denied[0].label === "DENIED" (text — colour-blind safe label)
  - denied[0].background resolves to --color-danger-bg
    (#fef2f2 in light, #3f1d1d in dark — read the CSS variable and
    confirm they match)
  - denied[0].borderColor resolves to --color-danger-line
  - denied[0].reason is non-empty (denial reason from policy)
```

### 4.4 Artifact attachment

```text
Pre-req: provoke the assistant to attach an artifact. The reference
prompt is "Summarise the JWT flow and save it as jwt-flow.md."
Backend will only do this if the runner+MCP stack supports artifact
emission; if it does not, mark this AC as DEFERRED in the report.

Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    [...document.querySelectorAll('[data-test="artifact-chip"]')]
      .map(el => ({
        name: el.querySelector('[data-test="artifact-name"]')?.textContent,
        size: el.querySelector('[data-test="artifact-size"]')?.textContent,
        href: el.getAttribute('href') ?? el.querySelector('a')?.href,
      }))

PASS criteria:
  - At least one chip rendered with name endsWith ".md".
  - name uses mono font (check fontFamily on chip's name node).
  - href routes to /v1/artifacts/<uuid>; clicking issues a request
    that resolves to a 302 (observe via read_network_requests).
```

### 4.5 Closed-session gate

```text
Pre-step: close a seeded session via
  curl -X POST $BACKEND/v1/sessions/$SID/close

Tool: mcp__claude-in-chrome__navigate → /s/<that sid>
Tool: mcp__claude-in-chrome__javascript_tool
  expression:
    ({
      input_disabled: document.querySelector('[data-test="message-input"]')?.disabled,
      send_disabled:  document.querySelector('[data-test="message-send"]')?.disabled,
      indication: document.querySelector('[data-test="closed-session-indication"]')?.textContent,
    })

PASS criteria:
  - input_disabled === true
  - send_disabled === true
  - indication matches /closed/i
```

### 4.6 Assistant-failure path

```text
Trigger: docker kill the runner container for the active session mid-stream.
  docker ps --filter "label=kloc.session=$SID" -q | xargs docker kill

PASS criteria:
  - The analyst's bubble remains visible in the thread.
  - The assistant bubble shows an unfinalized / error marker and a
    retry affordance ([data-test="retry-message"]).
  - get_page_text contains a clear failure indication.
```

---

## 5. UC5 — `beh.recover-from-disconnect`

**Recovery window = 5 minutes** (ExecutionRegistry event-ring TTL,
10 000-event capacity, retained 5 min after RUN_FINISHED). The 30 s
runner-heartbeat timeout governs runner-eviction, not stream replay.
Inside the 5-minute window the frontend MUST resume via cursor replay
(`GET /v1/sessions/{id}/stream?run_id=...&last_event_id=<seq>`); beyond
it, fall back to `GET /v1/sessions/{id}/messages`.

**Disconnect technique**: Chrome DevTools Network → "Offline" via
`mcp__claude-in-chrome__javascript_tool` (override `navigator.onLine`,
dispatch `'offline'`/`'online'` events, or abort the active EventSource
directly). To force the "exceeded" path without waiting 5 min, restart
the backend (which clears the in-process ring) and reload.

### 5.1 Reconnect within window

```text
Pre-step: open a new session, type a question that produces a longer
          reply (e.g. "Walk me through the booking pipeline").

While the assistant is streaming:
  Tool: mcp__claude-in-chrome__javascript_tool
    expression: |
      // Snapshot the current visible assistant text BEFORE the cut.
      window.__preCut = document.querySelector('[data-test="assistant-bubble"]')?.textContent ?? "";
      // Force the SSE EventSource to close.
      // The frontend should expose the underlying client; if not, drop
      // the network via DevTools throttling instead.
      if (window.__aguiClient?.abort) window.__aguiClient.abort();
      "cut";

  Wait ~3 seconds, then:
  Tool: mcp__claude-in-chrome__javascript_tool
    expression: |
      // Re-trigger reconnect by toggling visibilityState or by calling
      // the client's reconnect path. Implementation-dependent — read
      // src/lib/agui-http-agent.ts to find the exposed hook. Otherwise
      // just navigate(0).
      location.reload();
      "reloading";

After reload:
  Tool: mcp__claude-in-chrome__read_network_requests
    → expect a GET /v1/sessions/<sid>/stream?run_id=...&last_event_id=<n>

  Tool: mcp__claude-in-chrome__javascript_tool
    expression: |
      ({
        post_text: document.querySelector('[data-test="assistant-bubble"]')?.textContent ?? "",
        tool_calls: document.querySelectorAll('[data-test="tool-call"]').length,
        artifacts:  document.querySelectorAll('[data-test="artifact-chip"]').length,
      })

PASS criteria:
  - The post-reload bubble text is a strict superset of window.__preCut.
  - tool_calls count is monotonically >= the count snapshotted pre-cut.
  - No tool-call card or artifact chip from the pre-cut snapshot is
    missing.
```

### 5.2 Completed-while-disconnected

```text
Pre-step: cut the connection but let the backend finish the reply
          (verify by polling GET /v1/sessions/<sid>/messages until the
          last message has finalized_at non-null).

Tool: reload the page.

PASS criteria:
  - The full finalized assistant Message is visible.
  - Input bar is enabled and not displaying any in-flight indicator.
```

### 5.3 Recovery-window exceeded

Two ways to trigger; prefer (B) — faster.

```text
(A) Wait > 5 minutes (slow): cut, then sleep > 5 min.

(B) Force eviction (fast, per team-lead amendment): cut, then restart
    the backend (the in-process ExecutionRegistry is process-local;
    restart clears it).
      docker compose -f docker-compose.yml restart backend
      # or: Ctrl+C then re-run uvicorn

Tool: reload the page after eviction.
Tool: mcp__claude-in-chrome__read_network_requests
  → expect GET /v1/sessions/<sid>/messages (fallback path); MUST NOT
    issue GET /stream?last_event_id=...

PASS criteria:
  - Thread renders from persisted messages only.
  - No streaming spinner / blinking caret in the final state.
  - The most recent assistant Message is fully visible if it was
    finalized; otherwise it is marked unfinalized with no replay.
```

---

## 6. Visual regression checks

Compare the live UI to the two reference artefacts. Treat any deviation
in the listed tokens as a defect.

### 6.1 Light theme

```text
Tool: mcp__claude-in-chrome__navigate → http://localhost:3000
Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    const cs = getComputedStyle(document.documentElement);
    const want = {
      '--color-ink':           '#0c0a09',
      '--color-ink-muted':     '#57534d',
      '--color-ink-faint':     '#a8a29e',
      '--color-line':          '#e7e5e3',
      '--color-line-strong':   '#d6d3d0',
      '--color-canvas':        '#ffffff',
      '--color-canvas-rail':   '#faf9f7',
      '--color-canvas-sunk':   '#f5f4f1',
      '--color-accent':        '#2b5cff',
      '--color-success':       '#15803d',
      '--color-warning':       '#b45309',
      '--color-danger-bg':     '#fef2f2',
      '--color-danger-line':   '#fecaca',
      '--color-danger-ink':    '#991b1b',
    };
    const mismatches = Object.entries(want)
      .filter(([k, v]) => cs.getPropertyValue(k).trim().toLowerCase() !== v);
    JSON.stringify({ mismatches });

PASS: mismatches.length === 0.
```

Font checks (light theme):

```text
Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    const sansEl = document.querySelector('h1') ?? document.body;
    const monoEl = document.querySelector('.mono, [data-test="session-id"]');
    ({
      sans: getComputedStyle(sansEl).fontFamily,
      mono: getComputedStyle(monoEl).fontFamily,
      lang: document.documentElement.lang,
      hasGoogleFonts: !!document.querySelector('link[href*="fonts.googleapis.com"]'),
    })

PASS:
  - sans contains "Geist"
  - mono contains "JetBrains Mono"
  - lang === "en"   (nfr.document-language)
  - hasGoogleFonts === false  (self-hosted via next/font)
```

Layout invariants:

```text
Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    const aside = document.querySelector('aside');
    const main  = document.querySelector('main');
    ({
      asideWidth: aside?.getBoundingClientRect().width,
      asideBg: getComputedStyle(aside).backgroundColor,    // canvas-rail
      threadMaxWidth: getComputedStyle(main.querySelector('[data-test="thread-column"]')).maxWidth,
    })

PASS:
  - asideWidth === 260
  - threadMaxWidth === "680px"
```

### 6.2 Dark theme

```text
Tool: mcp__claude-in-chrome__javascript_tool
  expression: |
    document.documentElement.dataset.theme = 'dark';  // or click in-app toggle
    document.documentElement.classList.toggle('dark', true);
    "switched";

Tool: re-run the token query from 6.1 with the dark column values from
the spec table:

  want_dark = {
    '--color-ink': '#f5f5f4',
    '--color-ink-muted': '#a8a29e',
    '--color-ink-faint': '#78716c',
    '--color-line': '#27272a',
    '--color-line-strong': '#3f3f46',
    '--color-canvas': '#1c1917',
    '--color-canvas-rail': '#18181b',
    '--color-canvas-sunk': '#27272a',
    '--color-accent': '#7c92ff',
    '--color-danger-bg': '#3f1d1d',
    '--color-danger-line': '#7f1d1d',
    '--color-danger-ink': '#fca5a5',
  };

PASS (per PM spec line 321–323):
  - Spec marks dark values as "guidance derived from img_1.png" not
    pixel-exact. Treat mismatches as soft warnings; the hard FAIL is
    only when the dark theme is visually indistinguishable from the
    light theme (e.g. background still white) or when the danger
    surface is not red on a dark backdrop.

Verify also:
  - prefers-color-scheme emulation toggles theme too:
    via DevTools-style override, set the media query at runtime
    (window.matchMedia is read-only; instead remove the localStorage
    override and trigger the system listener if exposed by the app).
```

### 6.3 Theme-toggle persistence

```text
Tool: click the in-app theme toggle. Reload the page. Re-read
document.documentElement.dataset.theme (or whatever attribute the app
uses). PASS: the chosen mode survives a reload (localStorage override
per PM spec line 41).
```

---

## 7. NFR checks (summary)

| NFR | How to verify | Where |
|-----|---------------|-------|
| `nfr.progressive-rendering` | `performance.now()` delta between submit and first non-empty assistant bubble — < 1000 ms (single-shot; flag as not-true-p95) | 4.1 |
| `nfr.history-page-size` | Single GET `/messages?limit=500` on a 500+ message session | 3.2 |
| `nfr.disconnect-recovery` | Cursor-replay GET path used after a mid-stream drop, no missing events | 5.1 |
| `nfr.tool-denial-affordance` | Card has BOTH danger token surface AND the literal "DENIED" label text | 4.3 |
| `nfr.document-language` | `document.documentElement.lang === "en"` | 6.1 |
| Bundle ≤ 200 KB (engineering) | After `npm run build`, sum gzip sizes under `.next/static/chunks/` reported for the landing route | Out-of-band; bash check in 0.4 ext. |

Bundle-size check:

```bash
cd /Users/michal/dev/ai/kloc/kloc-agent/frontend
npm run build
# Inspect the per-route output table from `next build`; the line for
# `/` (or `/s/[id]`) should report First Load JS ≤ 200 KB gzipped.
```

---

## 8. CopilotKit absence (FE-SEC posture)

```bash
cd /Users/michal/dev/ai/kloc/kloc-agent/frontend
grep -RIn --color=never 'copilotkit\|CopilotKit' src package.json next.config.ts \
  | grep -v 'node_modules' || echo "OK: no copilotkit references"
```

PASS: zero matches in `src/`, `package.json`, or `next.config.ts`.
The CLAUDE.md still lists CopilotKit as the legacy stack — that
reference does not bind this rewrite per the PM spec (`In Scope` line
32: "CopilotKit is dropped").

---

## 9. Final reporting template

After running all sections, message `team-lead` with a verdict:

```text
QA validation result for kloc-agent-frontend

PASS / FAIL per use-case:
  UC1 browse-sessions     : PASS|FAIL  (note any sub-scenario)
  UC2 start-new-session   : PASS|FAIL
  UC3 resume-session      : PASS|FAIL  (3.2 may be DEFERRED)
  UC4 ask-assistant       : PASS|FAIL  (call out 4.3 and 4.4
                                        explicitly — denial + artifact)
  UC5 recover-from-disconnect : PASS|FAIL

PASS / FAIL per NFR:
  progressive-rendering   : PASS|FAIL  (latency_ms = ...)
  history-page-size       : PASS|FAIL|DEFERRED
  disconnect-recovery     : PASS|FAIL
  tool-denial-affordance  : PASS|FAIL
  document-language       : PASS|FAIL

Visual regression:
  light tokens            : PASS|FAIL  (list mismatches)
  dark theme              : PASS|FAIL  (soft / hard)
  layout invariants       : PASS|FAIL

CopilotKit absence       : PASS|FAIL

Failure repro steps (if any): <inline> with screenshots / page text.
```

On FAIL, attach exact reproduction steps and the failing check name so
developers can address it without re-discovering the symptom.
