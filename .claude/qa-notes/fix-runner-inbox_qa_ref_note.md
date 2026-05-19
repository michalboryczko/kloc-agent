# QA Reference Note — fix-runner-inbox

**Spec:** `docs/specs/fix-runner-inbox.md`
**Task folder:** `docs/tasks/fix-runner-inbox/`
**Branch:** `master` (current HEAD `f93b843de`, contains lead's WIP-committed dirty docs only)
**Baseline ("pre-fix") SHA:** `328ff1423a51ab71f239c92b4617baa1f214fed9` — parent of WIP `f93b843de`; commit subject `chore: add .gitignore, untrack vendor/build artifacts, unify .env`. Lead referred to this commit as `8351d45e` in the brief; that short SHA does not resolve in this clone, but the commit subject matches exactly and `git rev-parse f93b843de^` yields `328ff1423…`. Use `328ff1423` for the "must FAIL on baseline" runs in §9.
**QA owner:** qa
**Last updated:** 2026-05-19 (lead-approved Phase-2 revision)

This note is the single source of truth for what proves the feature done.
Section §6 (structural greps) is a hard gate — the feature is **not closed**
until every grep listed there returns zero matches.

---

## 0. Status of open questions (lead + architect resolutions 2026-05-19)

| # | Topic | Resolution |
|---|---|---|
| 1 | AC1 numeric TTFT SLO | `[QA-default — accepted]` Architect set no number. AC1.b stays manual smoke after deploy. |
| 2 | AC4 perf measurement | **Lead-approved & overrides architect.** Architect plan called AC4 "manual / out of automated scope"; lead OVERRIDE: keep the automated assertion (N=100, warm-up 10, p95 over the latter 90, report median + p99). Test file: `tests/integration/test_inbox_perf.py`. Architect's manual approach is fine as a complementary op-check in §7. |
| 3 | AC5 polling cadence verification | **Approved plan-B** — assert `pgmq.read` call count in unit test. tcpdump / `log_statement='all'` documented as manual op-check only. |
| 4 | AC8 force-evict mechanism | **Architect resolution — option (a) only.** Use `await registry._on_evict(entry)` (registry hook) directly. Drop the option-(c) `docker kill` e2e variant. Deterministic, fast, exercises the exact code path that was buggy. |
| 5 | AC3 backend-restart simulation | **Approved plan-B** — drop in-process `app.state.runner_registry` and reconstruct; full container restart remains manual op-check in §7. |
| 6 | AC10 "remains green" | **Approved framing — explicit:** AC10 applies to the **post-deletion** suite. Deletions in §4 land as part of the fix; reviewers must not flag deleted file paths as "missing". Phase-6 `pytest tests/ -q` runs **after** deletions, on the fix branch only. |
| 7 | AC11 log-string assertion | **Locked literal:** `log.info("pgmq extension ready")` per architect plan T04. Asserted via `caplog` at INFO level in `tests/unit/test_lifespan_boot.py::test_lifespan_logs_pgmq_extension_ready`. |
| 8 | AC13 session-close hook point | **Architect resolution — DEFERRED FROM SCOPE.** `drop_inbox_queue` helper exists (T03) but **no caller is wired in this task**. AC13 is a known gap; flagged for follow-up. **No automated test will be written; document as known gap in §1 and §10.** |

Additional codebase findings forwarded to architect (lead acknowledged 2026-05-19):

- `tests/integration/test_runner_inbox.py` does **not** exist in `master@328ff1423`. Plan's deletion list will be corrected by the architect.
- Cleanup needed beyond plan §4/§6: `tests/unit/test_stream.py` (`FakeRegistryEntry.inbox` field), `tests/integration/test_rehydrate.py:49` (`inbox_poll_timeout_s=25`), `tests/fixtures/hydration_payload_sample.json:20` (`inbox_poll_timeout_s: 25`), and `HydrationPayload.inbox_poll_timeout_s` field in `src/db/models.py:333`. Captured in §4 below.

---

## 1. Acceptance-criteria → test mapping

| AC | Statement | Proving artifact | Type |
|----|-----------|------------------|------|
| AC1 | Warm runner picks up 2nd msg < 1s; no pending indicator afterwards | `tests/integration/test_inbox_listen_notify.py::test_warm_runner_consumes_second_message_within_1s` | automated |
| AC1.b | "Normal TTFT envelope" (no cold-spawn penalty) | **manual smoke after deploy**: send msg in dev UI, observe TTFT visually. Spec does not pin a numeric SLO. *[QA flagged in Phase 1 review — no SLO number]* | manual |
| AC2 | Pending msg survives runner eviction | `tests/integration/test_inbox_survives_eviction.py::test_pending_message_survives_warm_idle_eviction` | automated |
| AC3 | Pending msg survives backend restart | `tests/integration/test_inbox_survives_eviction.py::test_pending_message_survives_registry_rebuild` (in-process proxy for restart) + **manual op-check** for full container restart | automated proxy + manual |
| AC4 | Wake-up latency p95 ≤ 50 ms over N=100 | `tests/integration/test_inbox_perf.py::test_wake_latency_p95_under_50ms` (N=100, warm-up=10, p95 over the latter 90; reports median + p99). **Lead override:** automated gate, not manual-only as the architect plan suggested. Complementary manual op-check #7 in §7. | automated |
| AC5 | No periodic poll faster than 30s fallback | `tests/messaging/test_inbox_consumer.py::test_consumer_polls_at_most_once_per_fallback_window` (asserts `pgmq.read` call count over a faked 30s+ idle window) | automated |
| AC6 | Old code paths deleted | §6 structural greps below | automated (grep gate) |
| AC7 | `test_inbox_redelivery.py` passes | `tests/integration/test_inbox_redelivery.py::test_unacked_message_redelivered_to_next_consumer_after_vt` | automated |
| AC8 | `test_inbox_survives_eviction.py` passes (exact bug repro) | `tests/integration/test_inbox_survives_eviction.py` — full file | automated |
| AC9 | `tests/messaging/*` unit tests pass | `tests/messaging/test_pgmq_topology.py`, `tests/messaging/test_inbox_producer.py`, `tests/messaging/test_inbox_consumer.py` | automated |
| AC10 | Post-deletion suite green | `pytest tests/ -q` run on the fix branch **after** §4 deletions land. Reviewers must not flag deleted files as "missing"; "remains green" refers to the post-deletion state only. | automated |
| AC11 | `pgmq extension ready` logged at boot | `tests/unit/test_lifespan_boot.py::test_lifespan_logs_pgmq_extension_ready` (caplog) + **manual `docker compose up` op-check** | automated + manual |
| AC12 | `\dx` shows pgmq; `pgmq.metrics_all()` works | **manual op-check** §7 | manual |
| AC13 | `session.close` drops `inbox_{sid}` queue | **Known gap — deferred from scope.** Architect plan T03 ships `drop_inbox_queue(...)` helper but no caller is wired in this iteration. AC13 will not pass on its own merits; QA does not write a test that asserts on an unwired hook. Follow-up task required to wire the caller. See §10. | deferred |
| AC14 | `docs/usdl/composition.xml` updated | reviewer-1 owns; out of QA scope | n/a |
| AC15 | `docs/usdl/topology.xml` updated | reviewer-1 owns; out of QA scope | n/a |
| AC16 | `docs/usdl/interfaces.xml` updated | reviewer-1 owns; out of QA scope | n/a |
| AC17 | task README status = Closed | reviewer-1 / PM owns; out of QA scope | n/a |

**Open items** (PM-flagged, do not block QA): `ifc.inbox-ack` mode mismatch,
orphan `com.flow.runner-inbox-consume`, `behavior.xml` not updated. None of
these affect tests.

---

## 2. Regression tests (load-bearing — must FAIL on baseline, PASS on fix)

### 2.1 `tests/integration/test_inbox_redelivery.py`

**Purpose:** prove the PGMQ visibility-timeout redelivery contract.

**Scenario:**
1. Produce a `user_message` via `send_user_message(conn, sid, run_id, msgs)`.
2. Consumer A reads it via `pgmq.read(queue, vt=2, qty=1)` but does **not** ack.
3. Consumer A disconnects (closes asyncpg connection).
4. Wait `vt + epsilon` (≈2.5s).
5. Consumer B opens a fresh asyncpg connection and reads from the same queue.
6. Assert Consumer B receives the **same** `msg_id` and payload.
7. Consumer B acks via `delete_message`.
8. Consumer C reads → returns `None` (queue empty).

**Baseline behaviour (pre-fix):** test does not exist → vacuous pass. After
the fix, this test exists and uses the PGMQ contract. The baseline
"must FAIL" framing applies to AC8, not this one.

### 2.2 `tests/integration/test_inbox_survives_eviction.py`

**Purpose:** the bug ticket reproduction. This is the single most
load-bearing test in this feature.

**Scenario (`test_pending_message_survives_warm_idle_eviction`):**
1. Boot FastAPI app via `TestClient`/`httpx.AsyncClient`.
2. `POST /v1/sessions` → `session_id`.
3. `POST /v1/sessions/{sid}/stream` with first message "hi"; assert 200,
   assert SSE stream completes a first turn.
4. Take the runner via `app.state.runner_registry.get(sid)`; capture `runner_id_A`.
5. **Force-evict the warm runner.** [QA-default — confirm with architect]
   - Method (a): `await app.state.runner_registry._on_evict_for_session(sid)`
     (or equivalent registry hook).
   - Fallback method (b): `await registry._remove_entry(sid)` +
     `await runner.terminate(handle)` against the captured handle.
6. Assert `app.state.runner_registry.get(sid)` returns `None`.
7. Connect to Postgres directly:
   ```sql
   SELECT count(*) FROM pgmq.q_inbox_{sid_slug};
   -- (queue created lazily by step 8 below; if AC requires step 6's
   -- queue-pre-existence, send msg 2 BEFORE eviction instead.)
   ```
8. `POST /v1/sessions/{sid}/stream` with second message "list flows".
9. Assert: a fresh runner spawned (new `runner_id_B != runner_id_A`).
10. Assert: SSE stream emits assistant content for "list flows" within
    a normal warm TTFT envelope.
11. Assert: `SELECT count(*) FROM pgmq.q_inbox_{sid_slug}` returns 0
    after the new runner acks (poll until 0 or timeout 10s).
12. Assert: no `runner_warm_idle_evicted` audit row for `runner_id_B`
    within the test window (i.e. the new runner didn't have to be
    evicted to drain the backlog — the bug's rescue path).

**Variant `test_pending_message_survives_registry_rebuild` (AC3 automated proxy):**
1. Same setup through step 5.
2. Drop `app.state.runner_registry` and re-construct from `Settings`
   (mimics a fresh backend boot without an HTTP-level restart).
3. Send message 2 via `POST /stream` → fresh spawn picks up the backlog.

**Baseline ("master pre-fix") expectation:**
- The current code has `entry.inbox.put` in `stream_post` (`src/api/stream.py:97`)
  and `iter_inbound` in `runner/channel.py:84-119`. After eviction at step 5,
  the in-memory `asyncio.Queue` is gone with the `RegistryEntry`. The msg
  in step 8 lands on a brand-new `RegistryEntry.inbox`, drained by a
  brand-new runner — so step 10 actually passes on master too (the bug
  symptom is "*old* runner ignores msg", not "new runner ignores msg").
- To prove the bug is the *queue-identity drift*, the test must instead
  evict mid-poll: enqueue msg 2 onto the **old** entry, then evict the
  old runner, then verify msg 2 is unrecoverable on master but
  recoverable on the fix branch.
- **The test is therefore structured as: enqueue → evict → verify
  recovery.** On master, msg 2 sits in the dead `asyncio.Queue` and is
  lost; on fix branch, it sits in PGMQ and is drained by the new runner.

**Baseline-commit note (lead-confirmed 2026-05-19):**
Baseline = `328ff1423a51ab71f239c92b4617baa1f214fed9`, parent of WIP
`f93b843de`. Lead referenced this commit as `8351d45e` in the brief
(short SHA does not resolve in this clone, but the commit subject —
`chore: add .gitignore, untrack vendor/build artifacts, unify .env` —
matches). The two regression tests must **fail** when run against
`328ff1423`'s code and **pass** when run against the fix branch.
Procedure documented in §9.

---

## 3. New unit tests

### 3.1 `tests/messaging/test_pgmq_topology.py`

- `test_ensure_extension_idempotent`: calling `ensure_extension(conn)` twice does not raise.
- `test_ensure_inbox_queue_idempotent`: two calls return the same queue name; underlying `pgmq.create_queue` does not raise on the second.
- `test_inbox_queue_name_strips_dashes`: UUID-formatted `session_id` produces a SQL-identifier-safe queue name.
- `test_inbox_queue_name_is_deterministic`: same `session_id` → same name.

### 3.2 `tests/messaging/test_inbox_producer.py`

- `test_send_user_message_writes_readable_row`:
  send via `send_user_message(conn, sid, run_id, msgs)`, then
  `SELECT msg_id, message FROM pgmq.read('inbox_<slug>', 30, 1)` and
  assert payload round-trips (`type`, `run_id`, `messages`).
- `test_send_user_message_fires_notify`:
  open a second connection, `LISTEN inbox_<slug>`, call `send_user_message`,
  assert the listener wakes within 100 ms with the expected channel name.
- `test_send_user_message_returns_msg_id`: returned `msg_id` matches the
  one visible via `pgmq.read`.

### 3.3 `tests/messaging/test_inbox_consumer.py` (new — covers `consume_inbox` + `delete_message`)

- `test_consumer_yields_payload_after_send`: producer pushes, consumer
  yields the parsed dict within 200 ms.
- `test_delete_message_acks_in_pgmq`:
  produce → consume → `delete_message` → `pgmq.read` returns nothing.
- `test_consumer_polls_at_most_once_per_fallback_window` (AC5):
  with no `NOTIFY` traffic and queue empty for 35 s (using `asyncio` time-scaling
  or `freezegun`), assert `pgmq.read` is called ≤ 2 times in that window
  (1 initial + 1 fallback at 30 s).
- `test_consumer_reconnects_on_connection_drop`:
  kill the asyncpg connection mid-loop; verify the consumer recovers and
  drains a freshly-enqueued message within 2 s.

### 3.4 `tests/integration/test_inbox_listen_notify.py` (sanity / AC1)

- `test_notify_wakes_listener_within_few_ms` (AC4 sanity):
  single `pgmq.send` + `NOTIFY`, listener wake-up < 50 ms.
- `test_warm_runner_consumes_second_message_within_1s` (AC1):
  full app fixture; first turn completes; second `POST /stream` arrives;
  assert AG-UI `TEXT_MESSAGE_CONTENT` event observed via SSE within 1 s
  of the second POST returning 200.

### 3.4b `tests/integration/test_inbox_perf.py::test_wake_latency_p95_under_50ms` (AC4 — lead override)

- N=100 sends with `await asyncio.sleep(0.01)` between each;
- record `recv_ts - send_ts` per message;
- discard first 10 (warm-up); assert `p95 ≤ 50 ms` against the
  remaining 90; report median + p99 in test output for triage.
- Skip under `KLOC_CI_FAST=1` to avoid hardware-jitter flakes; see §5.

### 3.5 `tests/unit/test_lifespan_boot.py::test_lifespan_logs_pgmq_extension_ready` (AC11)

- Boot `create_app()` lifespan in a unit context with `caplog`.
- Assert a record at `INFO` level whose message equals the literal string
  `pgmq extension ready` (architect-locked literal from plan T04
  `log.info("pgmq extension ready")`) is emitted exactly once.

### 3.6 *(removed)* AC13 session-close test

Architect deferred AC13 from scope (plan T03 ships `drop_inbox_queue`
helper but no caller is wired in this iteration). **No test will be
written** — asserting on an unwired hook would always fail. AC13 is
tracked as a known gap in §10, requires a follow-up task to wire the
caller (likely from `DELETE /v1/sessions/{id}` or session-state
transition to `closed`).

---

## 4. Tests to delete

The following tests/fixtures are tied to the removed in-memory inbox path
and must be removed by the dev as part of the feature (not by QA):

| Path | Reason |
|------|--------|
| `tests/unit/test_registry.py` lines 259–300 (4 tests: `test_inbox_get_returns_none_when_no_entry`, `test_inbox_get_times_out_when_no_message`, `test_inbox_get_returns_queued_message`, `test_inbox_get_zero_timeout_returns_immediately`) | `inbox_get` deleted |
| `tests/unit/test_stream.py` — `FakeRegistryEntry.inbox` field + any test asserting on `inbox.put` | `RegistryEntry.inbox` deleted |
| `tests/integration/test_rehydrate.py:49` — `inbox_poll_timeout_s=25` in fixture | field removed from `HydrationPayload` |
| `tests/fixtures/hydration_payload_sample.json` — `inbox_poll_timeout_s: 25` key | same |

**Note on the brief's listed `tests/integration/test_runner_inbox.py`:** this
file does **not** exist in `master@f93b843de`. The brief was written
against a different commit. QA will not flag its absence.

**Comment cleanup (not strictly required, but improves grep-hit quality):**

- `tests/unit/test_event_bus.py:59,69` — comments mentioning "inbox" should be
  updated by the dev when the surrounding code changes.

---

## 5. Performance harness

Co-located with §3.4 — `test_wake_latency_p95_under_50ms`. Specifics:

- **Environment:** local `docker compose up postgres` (pg16-pgmq image).
- **N:** 100 messages.
- **Producer:** single coroutine, `pgmq.send` + `NOTIFY`, no `asyncio.sleep`
  beyond a 10 ms inter-send gap to avoid burst-coalescing of NOTIFY frames.
- **Consumer:** single `consume_inbox` async-iterator; records monotonic
  receive timestamp on each yield.
- **Measurement:** `delta_i = recv_ts_i - send_ts_i`.
- **Warm-up:** discard first 10 deltas.
- **Assertions:**
  - `p95(deltas[10:]) <= 0.050`
  - Test output includes `median`, `p95`, `p99`, and `max` for triage.
- **Skip condition:** if `KLOC_CI_FAST=1` set in env, mark `pytest.skip`
  (so a slow CI runner does not gate on a hardware-jitter SLO).

---

## 6. Structural greps (gate — must return zero matches after fix)

Run from repo root `/Users/michal/dev/ai/kloc/kloc-agent/`:

```bash
# 1. No old HTTP inbox endpoint paths in source
grep -rn "GET /internal/sessions" src/ runner/ | grep -i inbox
grep -rn "/sessions/{session_id}/inbox" src/

# 2. No inbox endpoint handler or registry queue method
grep -rn "def runner_inbox" src/
grep -rn "inbox_get" src/

# 3. No in-memory inbox queue on RegistryEntry
grep -rn "RegistryEntry.*inbox" src/
grep -rn "\.inbox\." src/
grep -rn "\.inbox =" src/
grep -rn "entry\.inbox" src/ runner/ tests/

# 4. Old runner-side long-poll loop gone
grep -rn "iter_inbound" src/ runner/ tests/
grep -rn "INBOX_POLL_TIMEOUT_S" src/ runner/

# 5. HydrationPayload field removed (if dev chose to remove it)
grep -rn "inbox_poll_timeout_s" src/ runner/ tests/

# 6. Sanity: free-form "inbox" in critical files
grep -n inbox src/api/internal.py
grep -n inbox src/runner_mgmt/registry.py
grep -n inbox runner/channel.py
```

**Gate:** every command above must return zero matches **except** the
free-form "inbox" greps may legitimately match strings like
`inbox_queue_name(...)` / `inbox_<slug>` / `q_inbox_*` in the new
`src/messaging/pgmq.py` module — that is **expected** and not a
violation. The intent of the gate is that nothing matching the OLD
shape (`entry.inbox`, `runner_inbox`, `iter_inbound`,
`INBOX_POLL_TIMEOUT_S`, `inbox_poll_timeout_s`, `inbox_get`) survives.

If a literal `inbox_*` queue helper lives in `src/messaging/pgmq.py`,
QA will exclude that file from the free-form greps with `--exclude` when
running the gate.

---

## 7. Operational verifications (manual)

Run after the dev's branch is merged and `docker compose up` completes:

1. **Compose stack health.**
   ```bash
   cd /Users/michal/dev/ai/kloc/kloc-agent
   docker compose up -d
   docker compose ps   # all services healthy
   ```
   Backend container log must include the literal line containing
   `pgmq extension ready` exactly once at startup.

2. **`pgmq` extension installed.**
   ```bash
   docker compose exec postgres psql -U kloc -d kloc_agent -c "\dx"
   # expect a row for `pgmq`
   ```

3. **`pgmq.metrics_all()` returns rows.**
   ```bash
   docker compose exec postgres psql -U kloc -d kloc_agent \
     -c "SELECT * FROM pgmq.metrics_all();"
   ```
   Expect at least zero rows (no queues yet is acceptable); the
   function must not error.

4. **Per-session queue created on first message.**
   - Open the frontend, create a session, send "hi".
   - Run:
     ```bash
     docker compose exec postgres psql -U kloc -d kloc_agent \
       -c "SELECT * FROM pgmq.list_queues();"
     ```
   - Expect a queue named `inbox_<session_id_no_dashes>`.

5. **Backend-restart durability (AC3 operational complement).**
   - Send a message; while the runner is busy, `docker compose restart backend`.
   - After backend comes back, observe that:
     - The pending row is still in `pgmq.q_inbox_<slug>`
       (`SELECT count(*) FROM pgmq.q_inbox_<slug>;` ≥ 1 before drain).
     - On next `POST /stream` from the FE, a fresh runner spawns and
       drains the row; FE receives the reply.

6. **Session close drops the queue (AC13) — DEFERRED.**
   Architect plan T03 ships `drop_inbox_queue` helper but the caller is
   NOT wired in this iteration. Skip this op-check for the current
   feature; revisit when the follow-up task wires the caller. The helper
   can still be exercised manually via `psql` calling `pgmq.drop_queue`
   to confirm it works in isolation.

7. **No 25-second long-poll churn (AC5 op-check).**
   ```bash
   docker compose exec postgres psql -U kloc -d kloc_agent \
     -c "ALTER SYSTEM SET log_statement = 'all';"
   docker compose exec postgres psql -U kloc -c "SELECT pg_reload_conf();"
   docker compose logs -f postgres | grep pgmq.read
   ```
   With no traffic: expect at most 1 `pgmq.read` call per ~30 s per
   active session. Reset `log_statement` to default after.

---

## 8. Out of acceptance scope (QA will not test)

Listed explicitly so the team can challenge me if any of these become
in-scope during review:

- **PgBouncer** / connection-pool tuning.
- **Per-session DB credentials.** Single shared role used by both
  backend and runner.
- **Multi-worker backend horizontal scaling.** `app.state` singletons
  remain process-local.
- **Migration/back-compat for in-flight Sessions.** Spec is explicit:
  clean cut; no migration of the deleted HTTP inbox to PGMQ.
- **Outbound channel** (`POST /internal/.../events`) — unchanged; audit
  log proves it healthy.
- **HMAC audit webhooks** — unchanged.
- **SSE delivery to browser** — unchanged.
- **Background sweep of orphan `inbox_*` queues** — out of scope per
  implementation-plan.md §7.
- **USDL doc updates** (AC14–AC17) — reviewer-1 owns; QA will not
  validate docs/usdl/*.xml content.
- **Behavioral spec updates** (`docs/usdl/behavior.xml`) — PM-flagged
  open item, not in scope for this task.

---

## 9. Phase-6 validation procedure

After all code reviews APPROVED, run in order:

```bash
cd /Users/michal/dev/ai/kloc/kloc-agent

# Pre-flight
git status                                           # working tree clean
git rev-parse HEAD                                   # record sha for report

# 1. Structural greps (§6)
bash -c 'set -e; \
  grep -rn "iter_inbound" src/ runner/ tests/ && exit 1; \
  grep -rn "INBOX_POLL_TIMEOUT_S" src/ runner/ && exit 1; \
  grep -rn "def runner_inbox" src/ && exit 1; \
  grep -rn "inbox_get" src/ && exit 1; \
  grep -rn "entry\.inbox" src/ runner/ tests/ && exit 1; \
  grep -rn "inbox_poll_timeout_s" src/ runner/ tests/ && exit 1; \
  echo "structural greps: PASS"'

# 2. Full suite
uv run pytest tests/ -q

# 3. Regression tests on baseline (must FAIL — proves regression coverage)
#    Baseline = 328ff1423 (parent of WIP f93b843de). The new regression
#    tests live on the fix branch; copy them onto baseline before running.
git stash
git checkout 328ff1423a51ab71f239c92b4617baa1f214fed9
git checkout master -- tests/integration/test_inbox_redelivery.py \
                        tests/integration/test_inbox_survives_eviction.py
uv run pytest tests/integration/test_inbox_redelivery.py \
              tests/integration/test_inbox_survives_eviction.py -q
# Expect: FAIL (in-memory inbox path makes msg unreachable post-eviction)
git checkout -- .
git checkout master
git stash pop

# 4. Perf harness
uv run pytest tests/integration/test_inbox_listen_notify.py::test_wake_latency_p95_under_50ms -q -s

# 5. Operational verifications (§7 — manual, run by QA before final sign-off)
```

Report format to lead:

- **PASS:** all of §1 mapping verified; §6 greps clean; §3/§3.4 perf within SLO;
  §7 ops checks green.
- **FAIL:** for each failed AC, include test name, expected vs actual,
  reproduction command, file:line evidence, and a suggested fix
  direction (not a fix patch).

Verification checkpoints run (skill `verification-checkpoints`):
```bash
python .claude/skills/verification-checkpoints/scripts/verification_checkpoints.py \
  new --feature=fix-runner-inbox --component=full --agent-id=qa
# work through each checkpoint; sign off; record summary file path.
```

---

## 10. Open questions — resolved 2026-05-19

All eight Phase-1 questions resolved (lead + architect). See §0 for the
resolution table. Notable outcomes:

- **AC4** kept as automated perf gate via lead override (architect plan
  marked it manual; lead overrode).
- **AC8** uses option (a) registry hook only — `docker kill` variant
  dropped per architect.
- **AC11** literal string locked to `pgmq extension ready` per plan T04.
- **AC13 is a known gap.** `drop_inbox_queue` helper exists (T03) but no
  caller is wired this iteration. Follow-up task required to wire the
  caller from session-close. Documented here so it does not get lost.

### Known gaps (carry forward to follow-up tasks)

| Gap | Owner | Suggested follow-up |
|---|---|---|
| AC13 — `drop_inbox_queue` caller unwired | follow-up | new task: "Wire `drop_inbox_queue` from session-close path" + add `tests/integration/test_session_close.py::test_session_close_drops_inbox_queue` |
| Behavior.xml not updated (PM open item 3) | PM follow-up | per architect plan §8 — not blocking this task |
| Orphan-queue background sweep | future | per implementation-plan.md §7 — not blocking |
