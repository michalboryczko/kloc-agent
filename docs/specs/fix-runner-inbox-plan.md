# Plan: fix-runner-inbox

Refines `docs/tasks/fix-runner-inbox/implementation-plan.md` into an
executable, dependency-ordered task list with strict file ownership and
a 2-developer parallel work-stream split.

- **Spec:** `docs/specs/fix-runner-inbox.md`
- **Implementation reference:** `docs/tasks/fix-runner-inbox/implementation-plan.md`
- **USDL spec drafts (post-task targets):** `docs/tasks/fix-runner-inbox/spec/{topology,interfaces,composition}.xml`
- **Branch:** `master` (no new branches)

## Resolutions to PM/lead open items

The lead requested explicit decisions on the following before plan
acceptance. All resolutions land in the task table below; this section
is the rationale + single source of truth.

| # | Item | Decision | Rationale |
|---|---|---|---|
| R1 | `ifc.inbox-ack` mode mismatch on `top.com.runner-to-postgres-inbox` | **Drop `ifc.inbox-ack` from `carries=`** (CHANGES.md option b). The ack stays as an operation in `ifc.inbox-bus` (consumers can call it) but the topology edge declares only `ifc.inbox-consume`. | Splitting the edge adds two communications for one socket. The runner holds one connection that both LISTENs and acks; modelling it as two edges misrepresents the topology. Spec-lint passes; the ack contract is still discoverable via the bus. |
| R2 | Orphan `com.flow.runner-inbox-consume` call-flow | **Remove it entirely** (CHANGES.md option b). | No operation can `governed-by` it (contract provider is `top.postgres`). Keeping orphan call-flows for "narrative completeness" rots; the consumer logic is documented in `cmp.runner.inbox-consumer.intent`, which is the right place. |
| R3 | `behavior.xml` updates | **No change** (per PM, per implementation-plan §8). | No user-visible behavior change. AC2/AC3/AC4 are operational invariants, not user-observable behaviors. |
| R4 | `vt` (visibility timeout) — setting vs hardcode | **Hardcode `vt=300` as a module-level constant in `runner/inbox_consumer.py`** (Stream B owns it). No `runner_pgmq_vt_s` setting added in v1. | Surfacing it would force Stream A to add a Settings field, Stream B to read it, and Stream A to plumb it into `HydrationPayload`. Three-touchpoint coupling for a constant nobody has needed to tune yet. Keep it a single Stream B constant; promote to a setting only when a real session legitimately exceeds 5 min. |
| R5 | `HydrationPayload.backend_url` removal | **KEEP** the field. | `runner/hooks/audit.py:54,301` and `runner/channel.py:36,89,131` are active consumers (HMAC webhook URL + outbound JSONL POST). Grep-verified during recon. Removing would break two healthy channels that are explicitly out of scope (decision.md §"Out of scope"). See D1 in the Drift table below. |
| R6 | `pg_dsn` source — reuse `database_url` vs add `runner_pg_dsn` | **Reuse `settings.database_url` directly in `src/api/stream.py:_build_hydration_payload`.** No new setting. | decision.md §"Constraints accepted" explicitly accepts a single shared DB role for PoC scale ("Per-session DB credentials" is listed as out of scope). Adding `runner_pg_dsn` now is YAGNI; revisit when per-role separation lands as a follow-up. Stream A owns this; no Stream B impact. |
| R7 | Image swap scope across 3 compose files | **Swap `docker-compose.yml` AND `docker-compose.smoke.yml`. `docker-compose.dev.yml` needs no change** — it has no `postgres` block (backend-only override that bind-mounts `src/` for hot reload). | Verified by reading all three files during recon. See D4 in the Drift table. |
| R8 | AC1 — TTFT for warm runner (QA item A1) | **Concrete bound: assistant's first AG-UI `TEXT_MESSAGE_CONTENT` (or first user-visible token) arrives within `2.0 s` after `POST /v1/sessions/{id}/stream` returns SSE-open** on a warm runner. Runner pickup of the PGMQ message must happen within `1.0 s` (already required by AC1 prose). | The 1 s pickup bound is given by AC1. TTFT-to-first-token then depends only on Gemini's first-token latency — empirically <2 s on warm Gemini 3.1 Pro. Stating `≤ 2.0 s` lets QA write a numeric assertion in the warm-runner smoke test. If real measurements push back, soften to 3 s in a follow-up — but pick a number now. |
| R9 | AC8 — eviction mechanism (QA item A2) | **Two-tier coverage:** (a) **`tests/integration/test_inbox_survives_eviction.py` (T19) uses `await registry._on_evict(...)` directly** — exercises the eviction → respawn path without time advancement, fast and deterministic. (b) **Add `tests/e2e/test_inbox_survives_warm_idle_eviction.py` (T19b, new task)** that drives real warm-idle eviction by setting `RUNNER_WARM_IDLE_S=2` and waiting past the timer — exercises the production rescue path described in problem.md. | QA recommendation accepted. Splitting unit vs e2e gives fast feedback in CI (T19) plus the actual symptom reproduction (T19b). `docker kill` is unnecessary — `_on_evict` is the same code path warm-idle calls, so (a) exercises identical logic. |
| R10 | AC11 — exact log message string (QA item A3) | **Emit `log.info("pgmq extension ready")`** verbatim from `src/main.py` lifespan after `ensure_extension` returns. No prefix, no formatting tokens. | Lets QA write `assert "pgmq extension ready" in caplog.text` on a lifespan unit test. Matching QA's suggested string verbatim avoids a follow-up rename round. |
| R11 | AC4 — p95 wake-up latency measurement (out-of-band confirm) | **Manual verification only in v1.** No automated perf test added. The `runner/inbox_consumer.py` fallback poll is `30 s` and `LISTEN/NOTIFY` is sub-ms locally; pathological p95 requires a real degradation that lab measurement cannot synthesize anyway. Documented in acceptance.md but no test task. | Already in v1 plan; restated here to close QA's "manual-only AC" callout. Add a perf task post-merge if regression suspected. |

Tasks affected: R1, R2 → T14. R4 → T11. R5 → T05 (no removal). R6 → T06. R7 → T01, T02 (T_dev=no-op). R8 → T19 (new DoD assertion). R9 → T19 split + new T19b. R10 → T04 (exact log string). R11 → none (out-of-band).

## Drift vs implementation-plan.md (must read first)

Recon turned up six concrete drifts. Devs **MUST** apply these as
corrections — the implementation-plan is the design document, this
plan is what to actually do.

| # | implementation-plan claim | Reality on disk | Corrective action |
|---|---|---|---|
| D1 | "Remove `backend_url` from `HydrationPayload` if no remaining consumer in `runner/`" | `runner/hooks/audit.py:54,301` and `runner/channel.py:36,89,131` actively consume `backend_url` for the outbound JSONL stream and HMAC webhook URL | **KEEP** `HydrationPayload.backend_url`. Do not remove. |
| D2 | "Delete `tests/integration/test_runner_inbox.py`" | File does not exist. Inbox unit tests live in `tests/unit/test_registry.py:259-303` (4 tests) | Delete the four `test_inbox_get_*` tests from `tests/unit/test_registry.py` instead |
| D3 | "Delete inbox-related assertions in `tests/runner_mgmt/test_registry.py`" | Path is `tests/unit/test_registry.py`; `tests/runner_mgmt/` does not exist | Same as D2 — the test module path is `tests/unit/test_registry.py` |
| D4 | "Swap the postgres image in docker-compose.yml" (single file) | Three compose files exist: `docker-compose.yml` (full `postgres` block), `docker-compose.smoke.yml` (postgres block with port override only), `docker-compose.dev.yml` (no postgres block — backend-only override) | Update `docker-compose.yml` and `docker-compose.smoke.yml`. `docker-compose.dev.yml` needs no change. |
| D5 | not addressed | `src/runner_mgmt/docker_runner.py:186-194` `send_user_message` is a stub that references `RegistryEntry.inbox` in its docstring + NotImplementedError message | Rewrite the docstring/message: dispatch now goes via `cmp.backend.messaging` / PGMQ; the stub stays as Protocol compliance |
| D6 | not addressed | `tests/fixtures/hydration_payload_sample.json` has `"inbox_poll_timeout_s": 25` and `tests/integration/test_rehydrate.py:49` constructs `HydrationPayload(inbox_poll_timeout_s=25, ...)` | When `HydrationPayload.inbox_poll_timeout_s` is removed, also update the fixture + `test_rehydrate.py:49` + any other constructor sites |

## Task breakdown (dependency order)

Each task: id, title, stream, files touched, blocked-by, definition-of-done.

| ID | Title | Stream | Blocked-by | DoD |
|---|---|---|---|---|
| **T01** | Swap postgres image to `quay.io/tembo/pg16-pgmq:latest` in `docker-compose.yml` and load `pgmq` via `shared_preload_libraries` | A | — | `docker compose up` brings postgres up; `psql ... -c '\dx'` lists `pgmq` |
| **T02** | Mirror the image swap into `docker-compose.smoke.yml` (override `image:` only — port overrides preserved) | A | T01 | `docker compose -f docker-compose.yml -f docker-compose.smoke.yml up postgres` brings up pgmq image on port 5433 |
| **T03** | Create `src/messaging/__init__.py` (empty) + `src/messaging/pgmq.py` with `ensure_extension`, `inbox_queue_name`, `ensure_inbox_queue`, `send_user_message`, `drop_inbox_queue` per implementation-plan §2 | A | T01 | All five functions exist; raw SQL via `asyncpg`; no `pgmq-python` dep |
| **T04** | Wire `ensure_extension` into `src/main.py` lifespan startup (one short-lived asyncpg connection); emit `log.info("pgmq extension ready")` verbatim (R10) after the extension is loaded | A | T03 | Backend boot emits exactly the string `pgmq extension ready` (assertable via `caplog.text`); lifespan fails loudly if extension load raises |
| **T05** | Rewrite `HydrationPayload` in `src/db/models.py`: add `pg_dsn: str` and `inbox_queue: str`; remove `inbox_poll_timeout_s`; **keep** `backend_url` (see D1) | A | — | Pydantic model validates new shape; old `inbox_poll_timeout_s` gone |
| **T06** | Update `src/api/stream.py:_build_hydration_payload`: set `pg_dsn=settings.database_url` (R6 — reuse existing field; no new Settings entry), set `inbox_queue=inbox_queue_name(session_id)`; remove `inbox_poll_timeout_s=25` arg | A | T03, T05 | Hydration JSON contains `pg_dsn` matching `settings.database_url` and `inbox_queue` matching `inbox_queue_name(session_id)`; no `runner_pg_dsn` setting introduced |
| **T07** | Update `src/api/stream.py:stream_post`: delete `entry.inbox.put(...)`; call `ensure_inbox_queue` + `send_user_message` via existing `get_sessionmaker()` so `pgmq.send` + `NOTIFY` happen in one tx | A | T03, T08 | Grep for `entry.inbox.put` in `src/` returns no matches |
| **T08** | Strip `RegistryEntry.inbox` + `inbox_get` from `src/runner_mgmt/registry.py`: remove field from dataclass, remove `inbox` kwarg from `_install_entry` construction, remove `RunnerRegistry.inbox_get` method | A | — | Grep for `inbox` in `src/runner_mgmt/registry.py` returns no matches |
| **T09** | Delete `GET /internal/sessions/{id}/inbox` (`runner_inbox` handler) from `src/api/internal.py`; keep `_PRE_RUN_BUFFER_CAP` and `_dispatch_frame` (outbound channel) | A | — | Grep for `inbox` in `src/api/internal.py` returns no matches |
| **T10** | Fix `src/runner_mgmt/docker_runner.py:186-194` (D5): rewrite `send_user_message` docstring + `NotImplementedError` message to reference `cmp.backend.messaging` / PGMQ instead of `RegistryEntry.inbox` | A | T08 | No references to `RegistryEntry.inbox` in `src/` |
| **T11** | Create `runner/inbox_consumer.py` per implementation-plan §5: `consume_inbox(pg_dsn, session_id, queue_name)` async iterator + `delete_message(...)`; single long-lived asyncpg connection with `LISTEN`, `pgmq.read(vt=VT_SECONDS, qty=1)`, fallback 30 s wake. Define `VT_SECONDS = 300` and `FALLBACK_POLL_S = 30.0` as module-level constants (R4 — no Settings field). | B | T05 | Module exposes the two callables; `VT_SECONDS = 300` is a module constant; reconnect loop on `PostgresConnectionError` / `OSError` |
| **T12** | Update `runner/__main__.py`: replace `async for inbound in channel.iter_inbound():` with `async for msg_id, payload in consume_inbox(pg_dsn=payload["pg_dsn"], session_id=session_id, queue_name=payload["inbox_queue"]):`. Ack via `delete_message` after `_run_one_turn` (success **and** error). Handle `payload["type"] == "shutdown"` by acking + breaking. Update module docstring (`5. enter the iter_inbound() long-poll loop` → PGMQ loop) | B | T11 | Grep for `iter_inbound` in `runner/` returns no matches |
| **T13** | Strip `iter_inbound` + `INBOX_POLL_TIMEOUT_S` from `runner/channel.py`; rewrite module docstring (`Three flows` → `Two flows`). `_stream_outbound` + `_heartbeat_loop` untouched | B | T12 | Grep for `inbox` in `runner/channel.py` returns no matches |
| **T14** | Promote spec drafts: copy `docs/tasks/fix-runner-inbox/spec/{topology,interfaces,composition}.xml` over `docs/usdl/{topology,interfaces,composition}.xml`. Apply R1 + R2 from resolutions table: (1) edit `topology.xml` — remove `ifc.inbox-ack` from `top.com.runner-to-postgres-inbox.carries` (leave `ifc.inbox-consume` only); (2) edit `composition.xml` — delete the `<call-flow id="com.flow.runner-inbox-consume">` block entirely. `behavior.xml` is **not** touched (R3). | B | — | All four USDL specs pass spec-lint; `grep "inbox-ack" docs/usdl/topology.xml` returns no match in `carries=`; `grep "runner-inbox-consume" docs/usdl/composition.xml` returns no match; behavior.xml byte-identical to pre-change |
| **T15** | Backend unit tests: write `tests/messaging/__init__.py` + `tests/messaging/test_pgmq.py` covering `inbox_queue_name`, `ensure_extension` idempotence, `ensure_inbox_queue` idempotence, `send_user_message` round-trip, `drop_inbox_queue`. Use a per-test asyncpg connection against the pgmq postgres | A | T03 | All `tests/messaging/*` pass |
| **T16** | Delete inbox unit tests in `tests/unit/test_registry.py` (lines 259-303: four `test_inbox_get_*` functions and the section header comment); remove `inbox` field from `_FakeEntry` in `tests/unit/test_stream.py` | A | T08 | `pytest tests/unit -q` green; no `inbox_get` references |
| **T17** | Update fixture `tests/fixtures/hydration_payload_sample.json` (remove `inbox_poll_timeout_s`, add `pg_dsn` + `inbox_queue`); update `tests/integration/test_rehydrate.py:49` constructor call accordingly (D6) | A | T05 | `pytest tests/integration/test_rehydrate.py -q` green |
| **T18** | Regression test 1: `tests/integration/test_inbox_redelivery.py` — produce a message, consumer reads but does not delete, consumer disconnects → next consumer receives the same message after `vt` | B | T11 | Test asserts redelivery within `vt + 5s` |
| **T19** | Regression test 2 (integration, fast): `tests/integration/test_inbox_survives_eviction.py` — spawn runner, enqueue user_message, **call `await registry._on_evict(...)` directly to trigger eviction (R9a)** (no time advancement), spawn fresh runner, assert pending count == 1 via `SELECT count(*) FROM pgmq.q_inbox_<slug>` before the second spawn, then assert the new runner processes the message and FE receives the reply. Also assert: pickup-to-first-event latency ≤ 1 s (R8 / AC1 1 s bound) | B | T11, T12 | Integration test passes; explicit pgmq row-count assertion before respawn |
| **T19b** | Regression test 2 (e2e, slow): `tests/e2e/test_inbox_survives_warm_idle_eviction.py` — set `RUNNER_WARM_IDLE_S=2` in test fixture, drive real warm-idle eviction by waiting past the timer (R9b). Assert the production rescue path described in problem.md. Marked `@pytest.mark.e2e` so CI can opt-in/skip | B | T11, T12, T19 | E2E test passes against a live docker-compose stack |
| **T20** | Full-suite green: `pytest tests/ -q` (excluding `e2e` mark) confirm AC10. `pytest -m e2e tests/e2e/` separately confirms T19b | A | T15, T16, T17, T18, T19, T19b | All non-e2e tests pass on master; e2e run passes against a live stack |

21 tasks total (T19 was split into T19+T19b per R9). T14 (USDL promotion) and T20 (full-suite gate) are the two non-code-mutation closers.

## File ownership table

**Hard rule:** every file is owned by exactly one stream. Past teams hit
build breaks from concurrent edits to the same file. If a file is not in
this table, neither stream may touch it.

| File | Owner | Reason |
|---|---|---|
| `docker-compose.yml` | A | T01 — postgres image swap |
| `docker-compose.smoke.yml` | A | T02 — image override |
| `docker-compose.dev.yml` | — | Not touched (no postgres block) |
| `src/messaging/__init__.py` | A | T03 — new module |
| `src/messaging/pgmq.py` | A | T03 — new module |
| `src/main.py` | A | T04 — lifespan extension load |
| `src/db/models.py` | A | T05 — HydrationPayload shape |
| `src/api/stream.py` | A | T06, T07 — producer + hydration |
| `src/runner_mgmt/registry.py` | A | T08 — strip inbox |
| `src/api/internal.py` | A | T09 — delete inbox endpoint |
| `src/runner_mgmt/docker_runner.py` | A | T10 — fix stale inbox docstring |
| `runner/inbox_consumer.py` | B | T11 — new module |
| `runner/__main__.py` | B | T12 — consume_inbox loop |
| `runner/channel.py` | B | T13 — strip iter_inbound |
| `docs/usdl/topology.xml` | B | T14 — spec promotion |
| `docs/usdl/interfaces.xml` | B | T14 — spec promotion |
| `docs/usdl/composition.xml` | B | T14 — spec promotion |
| `docs/usdl/behavior.xml` | — | Not touched (no user-visible change) |
| `tests/messaging/__init__.py` | A | T15 — new module |
| `tests/messaging/test_pgmq.py` | A | T15 — new tests |
| `tests/unit/test_registry.py` | A | T16 — delete inbox tests |
| `tests/unit/test_stream.py` | A | T16 — remove inbox from `_FakeEntry` |
| `tests/fixtures/hydration_payload_sample.json` | A | T17 — fixture update |
| `tests/integration/test_rehydrate.py` | A | T17 — constructor update |
| `tests/integration/test_inbox_redelivery.py` | B | T18 — new regression |
| `tests/integration/test_inbox_survives_eviction.py` | B | T19 — new regression (integration, fast) |
| `tests/e2e/test_inbox_survives_warm_idle_eviction.py` | B | T19b — new regression (e2e, slow) |

No file appears in both rows. Verified.

## Work-stream split

The split keeps backend-producer files in Stream A and runner-consumer
files in Stream B; the only test file with shared concerns is
`HydrationPayload` (Stream A owns it, including the runner-facing
fixture + rehydrate test).

### Stream A — backend producer, registry, boot, image swap

**Files (read-write):**
- `docker-compose.yml`
- `docker-compose.smoke.yml`
- `src/messaging/__init__.py` (new)
- `src/messaging/pgmq.py` (new)
- `src/main.py`
- `src/db/models.py`
- `src/api/stream.py`
- `src/runner_mgmt/registry.py`
- `src/api/internal.py`
- `src/runner_mgmt/docker_runner.py`
- `tests/messaging/__init__.py` (new)
- `tests/messaging/test_pgmq.py` (new)
- `tests/unit/test_registry.py`
- `tests/unit/test_stream.py`
- `tests/fixtures/hydration_payload_sample.json`
- `tests/integration/test_rehydrate.py`

**Tasks:** T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T15, T16, T17, T20

**Internal task ordering (intra-stream):**
T01 → T02 (image), T05 → T17 (HydrationPayload), T08 → T16 (registry), T03 → T04 + T15, T03+T05 → T06, T03+T08 → T07, T08 → T10.

### Stream B — runner consumer, USDL promotion, regression tests

**Files (read-write):**
- `runner/inbox_consumer.py` (new)
- `runner/__main__.py`
- `runner/channel.py`
- `docs/usdl/topology.xml`
- `docs/usdl/interfaces.xml`
- `docs/usdl/composition.xml`
- `tests/integration/test_inbox_redelivery.py` (new)
- `tests/integration/test_inbox_survives_eviction.py` (new)
- `tests/e2e/test_inbox_survives_warm_idle_eviction.py` (new)

**Tasks:** T11, T12, T13, T14, T18, T19, T19b

**Internal task ordering (intra-stream):**
T11 → T12 → T13, T11 → T18, T12 → T19 → T19b. T14 is independent and can land first.

**Cross-stream dependencies:**
- T11 (Stream B) blocked by T05 (Stream A) — needs the new `HydrationPayload` shape to know which fields to consume.
- T19 (Stream B) blocked by T07 (Stream A) — eviction-survival test calls `stream_post`, which must already be on the PGMQ producer path.
- T20 (Stream A, the final gate) is blocked by Stream B's T18, T19 as well as A's own test work.

## Test plan inventory

### NEW tests

| File | Type | Asserts |
|---|---|---|
| `tests/messaging/test_pgmq.py` | unit | `inbox_queue_name(uuid)` is deterministic + valid SQL identifier; `ensure_extension` is idempotent; `ensure_inbox_queue` is idempotent; `send_user_message` writes a row visible to `pgmq.read`; `drop_inbox_queue` removes the queue (AC9) |
| `tests/integration/test_inbox_redelivery.py` | integration | Producer sends; consumer reads (no ack) and disconnects; second consumer receives the same payload after `vt` (AC7) |
| `tests/integration/test_inbox_survives_eviction.py` | integration | Full reproduction (fast): spawn, enqueue, `await registry._on_evict(...)` (R9a), verify pending count == 1 via `SELECT count(*) FROM pgmq.q_inbox_<slug>`, new spawn, assert delivery + pickup ≤ 1 s (AC8, partial AC1) |
| `tests/e2e/test_inbox_survives_warm_idle_eviction.py` | e2e | Same scenario but driven by real warm-idle timer with `RUNNER_WARM_IDLE_S=2` (R9b). Marked `@pytest.mark.e2e` |

### DELETED tests

| File | Section | Why |
|---|---|---|
| `tests/unit/test_registry.py` | lines 259-303 (4 `test_inbox_get_*` + section comment) | `inbox_get` and `RegistryEntry.inbox` are gone (AC6) |

### MODIFIED tests

| File | Change |
|---|---|
| `tests/unit/test_stream.py` | Remove `inbox: asyncio.Queue` field from `_FakeEntry`; remove the `inbox=asyncio.Queue()` kwarg in `_FakeRegistry.__init__`. Tests that exercise `stream_post` must be rewritten to assert a PGMQ producer call instead of `entry.inbox.put` (mock `src.messaging.pgmq.send_user_message`) |
| `tests/integration/test_rehydrate.py` | Line 49: replace `inbox_poll_timeout_s=25` with `pg_dsn="...", inbox_queue="..."` |
| `tests/fixtures/hydration_payload_sample.json` | Remove `inbox_poll_timeout_s`; add `pg_dsn`, `inbox_queue` |

### UNTOUCHED tests

`tests/unit/test_event_bus.py:59,69` — comments mention `inbox.put`
narratively; do **not** rewrite (the test exercises the bus, not the
inbox). Acceptable to leave the prose for now; clarifying re-words are
out of scope.

## Risk callouts

Carried over from implementation-plan.md §"Risks and mitigations", with
adjustments after recon:

| Risk | Source | Mitigation in this plan |
|---|---|---|
| Postgres `max_connections` saturation when N runners each hold a LISTEN connection | implementation-plan §risks | Documented only. Default 100 is fine for PoC-scale (≤ ~50 concurrent sessions). If hit, bump to 300 — out of scope here. |
| `NOTIFY` payload lost on reconnect | implementation-plan §risks | Fallback 30 s poll inside `consume_inbox` (T11) wakes the reader regardless. |
| `vt=300s` too short if a turn legitimately exceeds 5 min → double-processing on redelivery | implementation-plan §risks | Set `vt` constant at 300 s in `consume_inbox`. Surface as a setting only if a real session hits the cap — out of scope. |
| Tembo image `quay.io/tembo/pg16-pgmq:latest` unavailable in some envs | implementation-plan §risks | Fallback option 0b (stock postgres + init script) is documented but not the chosen path. T01 must verify image pulls cleanly on a dev box before merge. |
| Runner ↔ DB coupling regret | decision.md §constraints | Acknowledged debt. Reversible to RMQ later. |
| Concurrent stream-A edits to `src/api/stream.py` (T06 + T07 both touch it) | recon | Both tasks owned by Stream A, executed sequentially by the same developer. No cross-stream contention. |
| `backend_url` removal would break HMAC audit + outbound JSONL | D1 (recon) | Plan explicitly **keeps** `backend_url`. Documented in drift table. |
| Wake-up latency p95 ≤ 50 ms (AC4) | spec | Not asserted by a dedicated perf test in this plan. Validation is manual via `psql` `log_statement = 'all'` per AC5. If automated coverage needed, add a separate post-merge task. |

## Commit sequence

Each commit individually builds and tests pass. Follows
implementation-plan.md §10, refined for the drift-corrected scope.

1. `feat(postgres): swap image to pg16-pgmq in compose files; load extension at lifespan boot` — T01, T02, T04
2. `feat(messaging): pgmq queue helpers (ensure/send/drop) + NOTIFY` — T03 + T15
3. `feat(hydration): add pg_dsn + inbox_queue to HydrationPayload; drop inbox_poll_timeout_s` — T05, T17
4. `feat(stream): use pgmq for inbox; remove asyncio.Queue producer path` — T06, T07
5. `chore(registry): delete RegistryEntry.inbox + inbox_get; fix docker_runner stub docstring` — T08, T10, T16
6. `chore(api): delete GET /internal/sessions/{id}/inbox` — T09
7. `feat(runner): consume inbox from pgmq via LISTEN; remove iter_inbound` — T11, T12, T13
8. `test(inbox): redelivery + survives-eviction regression tests` — T18, T19, T19b
9. `docs(spec): promote pgmq inbox USDL specs to docs/usdl/` — T14
10. (gate) `pytest tests/ -q` green — T20 (no commit; verification step)

Commits 1–6 are Stream A. Commit 7 is Stream B. Commits 8–9 are Stream B
(T18/T19 need Stream A's commit 4 to be merged first). Commit 10 is the
joint gate.

## Verification checklist (mapped to acceptance.md)

| AC | Verification |
|---|---|
| AC1 (warm-runner second message) | T19 asserts pickup ≤ 1 s; TTFT-to-first-token ≤ 2 s (R8) verified manually against `docker-compose.smoke.yml` |
| AC2 (survives eviction) | T19 (fast/integration via `_on_evict`) + T19b (e2e via warm-idle timer) |
| AC3 (survives backend restart) | Implicit in PGMQ durability; documented in T19 as a manual follow-up |
| AC4 (p95 ≤ 50 ms) | Manual via `psql` query timing — out of automated scope |
| AC5 (no poll faster than 30 s) | T11 design; manual `log_statement = 'all'` check |
| AC6 (paths deleted) | `grep -r "inbox" src/api/internal.py src/runner_mgmt/registry.py runner/channel.py` returns empty |
| AC7 (redelivery test) | T18 |
| AC8 (eviction-survival test) | T19 |
| AC9 (`tests/messaging/*`) | T15 |
| AC10 (full suite green) | T20 |
| AC11 (compose up logs `pgmq extension ready`) | T04 |
| AC12 (`\dx` + `pgmq.metrics_all()`) | Manual against running stack |
| AC13 (session.close drops queue) | Out of scope for this iteration — `drop_inbox_queue` exists (T03) but no caller is wired in this task; flagged for follow-up |
| AC14, AC15, AC16 (USDL updates) | T14 |
| AC17 (README status → Closed) | Out of scope until merge — Lead updates post-merge |
