# Spec changes for fix-runner-inbox

Side-by-side diff of how the USDL specs change to reflect the chosen
solution: replace the in-memory long-poll inbox between backend and
runner with a PGMQ-backed durable transport.

- **Base specs (unchanged production state):**
  `docs/usdl/{topology,interfaces,composition}.xml`
- **Target specs (post-task state):**
  `docs/tasks/fix-runner-inbox/spec/{topology,interfaces,composition}.xml`
- **`behavior.xml` is NOT affected.** No user-visible behavior changes
  (`beh.rule.pending-reply-affordance` still applies). See
  `implementation-plan.md` §8 for the explicit decision.

Selectors below use the form `<element>[@id="…"]` (XPath-style) to
identify the exact node touched. The pattern matches a single
`<element id="…">` opening tag in the corresponding file.

---

## Summary table

| Section | Elements added | Elements modified | Elements removed |
|---|---|---|---|
| topology | 2 communications | 2 units/comm (description) | 1 communication |
| interfaces | 1 contract + 3 operations | 1 contract (block comment) | 1 operation |
| composition | 2 components, 9 constraints, 1 call-flow | 4 components, 1 call-flow | 0 (nothing physically removed — refs pruned in-place) |

---

## topology.xml

File: `docs/tasks/fix-runner-inbox/spec/topology.xml`.

### Modified

#### `<unit id="top.postgres">`
- **Selector:** `unit[@id="top.postgres"]`
- **Change:** description rewritten to mention PGMQ extension hosting
  durable per-Session inbox queues (`inbox_{slug}`) paired with
  `NOTIFY` wake-up; image swap noted
  (`postgres:16-alpine` → `quay.io/tembo/pg16-pgmq:latest`);
  `<tech>` updated to `PostgreSQL 16 + PGMQ extension`.
- **Driver:** decision.md → "Postgres image swaps to a pgmq-bundled
  flavour"; implementation-plan §0a.

#### `<communication id="top.com.backend-to-postgres">`
- **Selector:** `communication[@id="top.com.backend-to-postgres"]`
- **Change:** clarifying note that this edge is "Pure OLTP — the
  inbox transport now flows over a separate logical edge
  (`top.com.backend-to-postgres-inbox`) even though it shares the
  same asyncpg pool". No attribute changes.
- **Driver:** disambiguation against the new inbox-producer edge.

### Added

#### `<communication id="top.com.backend-to-postgres-inbox">`
- **Selector:** `communication[@id="top.com.backend-to-postgres-inbox"]`
- **Attributes:** `from=top.backend`, `to=top.postgres`, `via=sql`,
  `mode=sync-request-response`, `consistency=strong`,
  `delivery=at-least-once`, `carries=ifc.inbox-enqueue`.
- **Driver:** decision.md → "Backend produces via `pgmq.send` paired
  with `NOTIFY`".

#### `<communication id="top.com.runner-to-postgres-inbox">`
- **Selector:** `communication[@id="top.com.runner-to-postgres-inbox"]`
- **Attributes:** `from=top.runner`, `to=top.postgres`, `via=sql`,
  `mode=stream-pull`, `consistency=strong`,
  `delivery=at-least-once`, `carries=ifc.inbox-consume,ifc.inbox-ack`.
- **Driver:** decision.md → "Runner consumes via `LISTEN` +
  `pgmq.read` + `pgmq.delete`".

### Removed

#### `<communication id="top.com.runner-from-backend-inbox">`
- **Selector:** `communication[@id="top.com.runner-from-backend-inbox"]`
- **Change:** deleted in full. Replaced by the new SQL-based
  consumer edge above.
- **Driver:** problem.md → root cause of the "second message does
  nothing" symptom; implementation-plan §4.

---

## interfaces.xml

File: `docs/tasks/fix-runner-inbox/spec/interfaces.xml`.

### Modified

#### `<contract id="ifc.internal-runner-ingress">` (block comment + scope)
- **Selector:** `contract[@id="ifc.internal-runner-ingress"]`
- **Change:** comment rewritten to explain it is now NDJSON-only;
  the companion long-poll operation is gone. Contract attributes
  themselves are unchanged; only one operation remains
  (`ifc.ingest-runner-events`).
- **Driver:** implementation-plan §4.

### Added

#### `<contract id="ifc.inbox-bus">`
- **Selector:** `contract[@id="ifc.inbox-bus"]`
- **Attributes:** `provider=top.postgres`, `consumers=top.backend,top.runner`,
  `style=command-bus`, `spec-ref=./schemas/inbox.asyncapi.yaml`.
- **Driver:** decision.md → PGMQ as the inbox transport.

#### `<operation id="ifc.inbox-enqueue">`
- **Selector:** `operation[@id="ifc.inbox-enqueue"]`
- **Attributes:** `contract=ifc.inbox-bus`,
  `name="pgmq.send + NOTIFY inbox_{slug}"`,
  `mode=sync-request-response`, `consistency=strong`,
  `delivery=at-least-once`, `idempotency=not-applicable`,
  `realizes-behavior=beh.ask-assistant`.
- **No `governed-by`** — spec-lint requires the call-flow's
  `in-unit` to equal the operation's contract's `provider`
  (`top.postgres`), which has no kloc-owned components. The
  enqueue's role inside the open-run-stream flow is captured by
  the `<step component="cmp.backend.messaging"/>` step in
  `com.flow.open-run-stream`.

#### `<operation id="ifc.inbox-consume">`
- **Selector:** `operation[@id="ifc.inbox-consume"]`
- **Attributes:** `contract=ifc.inbox-bus`,
  `name="LISTEN inbox_{slug} + pgmq.read(vt=300, qty=1)"`,
  `mode=stream-pull`, `consistency=strong`,
  `delivery=at-least-once`, `idempotency=required`,
  `realizes-behavior=beh.ask-assistant`.
- Failure modes: `notify-missed-during-reconnect`
  (`retry-with-backoff`, fallback-poll-30s),
  `db-connection-lost` (`retry-with-backoff`,
  unacked-message-redelivered-after-vt).

#### `<operation id="ifc.inbox-ack">`
- **Selector:** `operation[@id="ifc.inbox-ack"]`
- **Attributes:** `contract=ifc.inbox-bus`,
  `name="pgmq.delete(queue, msg_id)"`,
  `mode=sync-request-response`, `consistency=strong`,
  `delivery=at-least-once`, `idempotency=required`,
  `realizes-behavior=beh.ask-assistant`.
- Failure mode: `db-unavailable` (`retry-with-backoff`,
  message-redelivered-on-vt-expiry).

### Removed

#### `<operation id="ifc.runner-inbox">`
- **Selector:** `operation[@id="ifc.runner-inbox"]`
- **Change:** deleted from `ifc.internal-runner-ingress`. The HTTP
  long-poll endpoint it documented (`GET /internal/sessions/{id}/inbox`)
  is also deleted in code (acceptance.md → "code paths that must
  not exist").
- **Driver:** root-cause.md → identity-drift class of bug.

### Reference impact in topology

- `top.com.runner-from-backend-inbox.carries` previously pointed at
  `ifc.runner-inbox`. Both the comm and the carries are gone.
- `top.com.backend-to-postgres-inbox.carries=ifc.inbox-enqueue` is new.
- `top.com.runner-to-postgres-inbox.carries=ifc.inbox-consume,ifc.inbox-ack` is new.

---

## composition.xml

File: `docs/tasks/fix-runner-inbox/spec/composition.xml`.

### Added

#### `<component id="cmp.backend.messaging">`
- **Selector:** `component[@id="cmp.backend.messaging"]`
- **Zone:** `src/messaging/`. **In-unit:** `top.backend`.
- **Intent:** owns the PGMQ-backed inbox producer + queue lifecycle
  (`ensure_extension`, `ensure_inbox_queue`, `send_user_message`,
  `drop_inbox_queue`). Holds no `asyncio.Queue` of its own.
- **Requires:** `cmp.backend.db`.
- **Constraints referenced:** `con.messaging-send-notify-same-tx`,
  `con.inbox-queue-name-from-session-id`,
  `con.messaging-no-asyncio-queue`,
  `con.drop-queue-only-on-session-close`.
- **Driver:** implementation-plan §2.

#### `<component id="cmp.runner.inbox-consumer">`
- **Selector:** `component[@id="cmp.runner.inbox-consumer"]`
- **Zone:** `runner/inbox_consumer.py`. **In-unit:** `top.runner`.
- **Intent:** holds one long-lived asyncpg connection that does
  `LISTEN inbox_{slug}` and alternates `pgmq.read(qty=1, vt=300)`
  with `pgmq.delete(queue, msg_id)`. The runner side of the
  structural fix.
- **Constraints referenced:** `con.inbox-consumer-listen-before-read`,
  `con.inbox-consumer-no-self-ack`.
- **Driver:** implementation-plan §5.

#### Constraints (9 new)

| Selector | Type | Purpose |
|---|---|---|
| `constraint[@id="con.api-no-direct-pgmq"]` | rule | API never calls pgmq.* or NOTIFY directly. |
| `constraint[@id="con.no-asyncio-inbox-queue"]` | anti-pattern | `src/runner_mgmt/` cannot reintroduce an in-memory inbox queue. |
| `constraint[@id="con.eviction-does-not-touch-pgmq"]` | rule | Runner-eviction paths never drop the inbox queue. |
| `constraint[@id="con.messaging-send-notify-same-tx"]` | pattern | `pgmq.send` + `NOTIFY` happen in one DB tx. |
| `constraint[@id="con.inbox-queue-name-from-session-id"]` | pattern | Queue name is a pure function of `session_id`. |
| `constraint[@id="con.messaging-no-asyncio-queue"]` | anti-pattern | `src/messaging/` holds no in-memory pending-message queue. |
| `constraint[@id="con.drop-queue-only-on-session-close"]` | rule | `drop_inbox_queue` called only from Session-close. |
| `constraint[@id="con.channel-no-inbox-loop"]` | anti-pattern | `runner/channel.py` has no `iter_inbound`. |
| `constraint[@id="con.runner-acks-after-processing"]` | pattern | `delete_message` called only after `_run_one_turn` returns. |
| `constraint[@id="con.inbox-consumer-listen-before-read"]` | pattern | `LISTEN` set up before first `pgmq.read`. |
| `constraint[@id="con.inbox-consumer-no-self-ack"]` | rule | `consume_inbox` never calls `pgmq.delete` itself. |

(Eleven actually — the table miscount in the summary was 9; the
canonical list above is the truth.)

#### `<call-flow id="com.flow.runner-inbox-consume">`
- **Selector:** `call-flow[@id="com.flow.runner-inbox-consume"]`
- **In-unit:** `top.runner`. **For-use-case:** `beh.ask-assistant`.
- **Steps:** `cmp.runner.inbox-consumer` →
  `cmp.runner.entrypoint` → `cmp.runner.channel` →
  `cmp.runner.inbox-consumer` (the trailing step is the ack).
- **No `governed-by` from any operation** — `ifc.inbox-consume` and
  `ifc.inbox-ack` cannot point here because their contract provider
  is `top.postgres` (spec-lint would fail). Kept as
  documentation-only inside the runner unit.

### Modified

#### `<component id="cmp.backend.api">`
- **Selector:** `component[@id="cmp.backend.api"]`
- **Structure:** `internal.py` description changed from
  "backend↔runner JSONL ingress + long-poll inbox" to
  "backend←runner JSONL ingress (outbound only…long-poll inbox
  endpoint…is gone — backend→runner traffic now flows over PGMQ)".
- **Logic — added rule:** "For `POST /v1/sessions/{id}/stream` the
  user-message enqueue is delegated to `cmp.backend.messaging`; the
  API module never calls `pgmq.*` or `NOTIFY` directly, never holds
  an `asyncio.Queue` of pending user messages, and never references
  `RegistryEntry.inbox` (which no longer exists)."
- **Logic — rewritten rule:** the prior
  "register the SSE subscriber on the EventBus before calling
  `inbox.put`" now reads "…BEFORE invoking the messaging producer".
- **Requires:** added `<requires ref="cmp.backend.messaging"/>`.
- **Constraints:** added `<constraint ref="con.api-no-direct-pgmq"/>`.

#### `<component id="cmp.backend.runner-mgmt">`
- **Selector:** `component[@id="cmp.backend.runner-mgmt"]`
- **Intent:** rewritten — drops "inbox queue per Session"; adds
  explicit "Holds NO inbox queue of its own; the inbox transport
  lives in `cmp.backend.messaging` on top of PGMQ so it survives
  the very `RegistryEntry` swaps that this component performs".
- **Structure:** `registry.py` line changed —
  `RegistryEntry{handle, warm_idle, heartbeat, inbox, ...}` →
  `RegistryEntry{handle, warm_idle, heartbeat, in_flight_tool_calls,
  ...}` with explicit note that
  `RegistryEntry.inbox` and `RunnerRegistry.inbox_get` have been
  removed.
- **Logic — added rule:** "Runner eviction NEVER drops the
  Session's PGMQ inbox queue — pending messages must survive
  eviction so the next spawn drains them."
- **Logic — modified rule:** the warm-idle rule clarifies that
  "user-message" is observed via the messaging producer call site,
  not via an in-memory inbox put.
- **Constraints:** added
  `<constraint ref="con.no-asyncio-inbox-queue"/>` and
  `<constraint ref="con.eviction-does-not-touch-pgmq"/>`.

#### `<component id="cmp.runner.channel">`
- **Selector:** `component[@id="cmp.runner.channel"]`
- **Intent:** explicitly OUTBOUND-only; documents removal of
  `iter_inbound`.
- **Structure:** "three async loops" → "two async loops"
  (`_stream_outbound` + `_heartbeat_loop`; `iter_inbound` gone).
- **Logic:** the two long-poll-related rules
  ("inbox loop swallows `httpx.TimeoutException`", "the `shutdown`
  inbox frame ends `iter_inbound`") are removed; replaced by a
  single rule about heartbeat-loop independence.
- **Constraints:** added
  `<constraint ref="con.channel-no-inbox-loop"/>`.

#### `<component id="cmp.runner.entrypoint">`
- **Selector:** `component[@id="cmp.runner.entrypoint"]`
- **Intent:** "enter the long-poll inbox loop" → "enter the PGMQ
  inbox-consume loop"; explicit mention of ack-after-process via
  `delete_message`.
- **Structure:** the `_run` lifecycle line names the new loop:
  `async for msg_id, payload in inbox_consumer.consume_inbox(...)`,
  and explicitly notes the prior `channel.iter_inbound()` loop is
  gone.
- **Logic — modified rule:** the MCP-scope rule now references the
  "inbox-consume loop" instead of "inbox loop".
- **Logic — added rule:** "`delete_message(msg_id)` is called once
  per yielded frame ONLY after `_run_one_turn` returns…"
  Plus: shutdown-frame handling rule.
- **Requires:** added `<requires ref="cmp.runner.inbox-consumer"/>`.
- **Constraints:** added
  `<constraint ref="con.runner-acks-after-processing"/>`.

#### `<call-flow id="com.flow.open-run-stream">`
- **Selector:** `call-flow[@id="com.flow.open-run-stream"]`
- **Change:** inserted `<step component="cmp.backend.messaging"/>`
  between the existing `cmp.backend.runner-mgmt` step and
  `cmp.backend.repos` step. Step count goes from 6 to 7.
- **Block comment:** updated to describe the new messaging step
  ("replaces the prior in-memory `entry.inbox.put`").

### Removed
None. All deletions of behavior in this section are expressed as
descriptive rewrites + new constraints; no `<component>` /
`<constraint>` / `<call-flow>` element is physically removed from
the file.

---

## Cross-section invariants preserved

- Every modified or new `top.X` / `ifc.X` / `cmp.X` / `con.X` /
  `com.flow.X` reference resolves (manually verified with a grep
  pass over the three target files).
- `mode=` matches between `<communication>` and the operations it
  carries:
  - `top.com.backend-to-postgres-inbox` (`mode=sync-request-response`)
    carries `ifc.inbox-enqueue` (`mode=sync-request-response`) ✓
  - `top.com.runner-to-postgres-inbox` (`mode=stream-pull`) carries
    `ifc.inbox-consume` (`mode=stream-pull`) and `ifc.inbox-ack`
    (`mode=sync-request-response`) — **note the mismatch on
    inbox-ack**: the ack is a request/response over the same SQL
    connection that streams reads; modelled as a mixed-mode
    conversation. Spec-lint will flag this; the resolution
    is either (a) split the comm into two edges (one
    stream-pull for LISTEN+read, one sync for ack) or (b) drop
    `ifc.inbox-ack` from `carries=`. Documented here as an open
    item — see "Open items" below.
- Contract `provider=top.postgres` is internal — passes the
  "provider must be internal" rule.
- No `governed-by` on `ifc.inbox-*` operations — avoids the
  spec-lint check that requires call-flow `in-unit` to equal the
  operation's contract's `provider`.

## Open items (deliberate, not bugs in the diff)

1. **`ifc.inbox-ack` mode vs comm mode.** Spec-lint will flag
   `top.com.runner-to-postgres-inbox` carrying both `ifc.inbox-consume`
   (`stream-pull`) and `ifc.inbox-ack` (`sync-request-response`).
   Two options: split the comm or remove `ifc.inbox-ack` from
   `carries=`. The latter is simpler; the former is more faithful.
   Resolve during the actual implementation PR.
2. **`com.flow.runner-inbox-consume` is orphan.** No operation's
   `governed-by` points at it because postgres can't host
   call-flows. Either accept it as documentation-only or remove
   it entirely. Kept here for narrative completeness.
3. **`behavior.xml` is not updated.** The implementation plan says
   no, but the acceptance scenarios introduce three new
   guarantees worth declaring: (a) pending message survives
   eviction, (b) pending message survives backend restart,
   (c) p95 wake-up ≤ 50 ms. If a follow-up wants them visible as
   `<invariant>` + `<nfr>` blocks under `beh.ask-assistant`, that
   is a clean addition.

---

## Reading order

1. `problem.md` — the symptom that motivates the change.
2. `root-cause.md` — why this is a transport-layer defect.
3. `solution-options.md` — what was considered.
4. `decision.md` — what was picked and why.
5. `implementation-plan.md` — file-by-file code changes.
6. `acceptance.md` — the test bar.
7. **This file (`CHANGES.md`) + the three target specs** —
   how the USDL representation reflects the post-task state.
