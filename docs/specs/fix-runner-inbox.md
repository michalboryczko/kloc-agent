# Feature Spec: fix-runner-inbox

## Problem

After a successful first turn in a Session, submitting a second message produces a permanently stuck pending-Assistant indicator — the response is never delivered to the browser, even though the Assistant's reply was actually written to the database. The root cause is identity drift in the in-memory inbox queue: the backend's `RegistryEntry.inbox` (an `asyncio.Queue`) is bound to a specific runner spawn, not to the session. When `stream_post` writes to one `RegistryEntry` while the runner's long-poll loop reads from a different one — created by a concurrent eviction, crash recovery, or spawn-lock retry — the user message becomes unreachable until the old runner is eventually killed and a fresh spawn drains the backlog. This is a structural defect in the transport layer, not a timing issue; no patch to the long-poll loop can fix it, because each side is individually correct.

## Decision

**Option E — PGMQ (Postgres queue extension) for the inbox channel.**

Queue identity moves off the per-spawn `RegistryEntry` and onto a durable Postgres row keyed by `session_id` alone. The identity-drift class of failure is structurally impossible when there is no in-memory inbox. No new infrastructure: Postgres is already in the compose stack; the change is an image swap to a pgmq-bundled flavour and a `CREATE EXTENSION pgmq`. The outbound runner-to-backend channel (chunked JSONL) is unchanged — the audit log proves it is healthy.

## Acceptance Criteria

### Functional — symptom no longer reproduces

**AC1.**
```gherkin
Scenario: second message is processed promptly by a warm runner
  Given a Session that has just completed a successful first turn
    And the warm runner is still alive within RUNNER_WARM_IDLE_S
  When the Analyst submits a second message
  Then the runner picks up the message within 1 second of submission
    And the Assistant's first visible content arrives within the normal
        TTFT envelope for a warm runner (no cold-spawn penalty)
    And no pending Assistant indicator remains after the reply finalises
```

### Functional — bug class structurally eliminated

**AC2.**
```gherkin
Scenario: pending message survives a runner eviction
  Given a Session whose warm runner has been evicted (warm-idle or crash)
        while a user_message is pending in its inbox
  When a new runner is spawned for the same Session
  Then the new runner consumes the pending user_message from PGMQ
    And the message is processed exactly once across the eviction
    And the resulting Assistant reply is delivered to the FE on the
        original Session's stream
```

**AC3.**
```gherkin
Scenario: pending message survives a backend restart
  Given the backend is restarted while a user_message is pending in
        the inbox queue for an active Session
  When the backend comes back up and a runner reconnects
  Then the runner consumes the pending message from PGMQ
    And the message is processed exactly once across the restart
```

### Performance

**AC4.** Wake-up latency from `pgmq.send` + `NOTIFY` on the backend to the runner's `consume_inbox` yielding the payload is **p95 ≤ 50 ms** on local docker compose. Verified by a test that sends 100 messages and records receive-time deltas.

**AC5.** The runner does not issue any periodic poll faster than the configured fallback interval (30 s). Verified by `log_statement = 'all'` on Postgres inspecting query rates, or by tcpdump.

### Code structure — paths that must not exist

**AC6.** All of the following are deleted and a grep returns no matches:
- `GET /internal/sessions/{session_id}/inbox` endpoint (`src/api/internal.py:runner_inbox`)
- `RunnerRegistry.inbox_get`
- `RegistryEntry.inbox: asyncio.Queue`
- `runner/channel.py:iter_inbound`
- `runner/channel.py` HTTP long-poll constants (`INBOX_POLL_TIMEOUT_S` if it has no other consumer)
- Any test that imports or asserts on the above

Verification command: `grep -r "inbox" src/api/internal.py src/runner_mgmt/registry.py runner/channel.py` returns no matches.

### Tests

**AC7.** `tests/integration/test_inbox_redelivery.py` passes — asserts a message is redelivered to a fresh consumer after the previous consumer disconnects without ack.

**AC8.** `tests/integration/test_inbox_survives_eviction.py` passes — exact reproduction of the reported bug:
1. Submit message 1; assert success.
2. Force-evict the runner.
3. Verify the pending message 2 is still in PGMQ (`SELECT count(*) FROM pgmq.q_inbox_<slug>` = 1).
4. Trigger a fresh spawn (new POST /stream).
5. Assert the new runner processes message 2 and the FE receives the Assistant reply.

**AC9.** `tests/messaging/*` unit tests pass — cover `ensure_extension`, `ensure_inbox_queue`, `send_user_message`, `delete_message`.

**AC10.** Existing suite (`pytest tests/ -q`) remains green.

### Operational

**AC11.** `docker compose up` brings the stack up with the pgmq-enabled Postgres image; backend logs `pgmq extension ready` at lifespan startup.

**AC12.** `psql` against the running postgres can `\dx` to list pgmq and `SELECT * FROM pgmq.metrics_all()` to inspect queue health.

**AC13.** On `session.close`, the corresponding `inbox_{sid}` queue is dropped. Verified by `SELECT * FROM pgmq.list_queues()` before and after.

### Documentation

**AC14.** `docs/usdl/composition.xml` reflects the new transport (new `cmp.backend.messaging` component; `cmp.backend.runner-mgmt` and `cmp.runner.channel` updated; 11 new constraints; updated `com.flow.open-run-stream`).

**AC15.** `docs/usdl/topology.xml` reflects the pgmq capability on postgres and the inbox arrows (new `top.com.backend-to-postgres-inbox`, `top.com.runner-to-postgres-inbox`; `top.com.runner-from-backend-inbox` removed).

**AC16.** `docs/usdl/interfaces.xml` describes `ifc.inbox-bus` with `ifc.inbox-enqueue`, `ifc.inbox-consume`, `ifc.inbox-ack`; `ifc.runner-inbox` is removed.

**AC17.** `docs/tasks/fix-runner-inbox/README.md` status updated to "Closed".

## Non-Goals

The following are explicit non-goals. Their absence is not a blocker for closing this task:

- PgBouncer / connection-pool tuning (follow-up if concurrency demands it).
- Per-session DB credentials (single shared role for PoC).
- Multi-worker backend horizontal scaling.
- Migration of in-flight Sessions from the deleted HTTP inbox to PGMQ (clean cut; any in-flight `asyncio.Queue` content is by definition lost on deploy).
- Outbound `POST /internal/.../events` channel — unchanged, healthy per audit log.
- HMAC audit webhooks — unchanged.
- SSE delivery to the browser — unchanged.
- A background sweep for orphaned `inbox_*` queues — out of scope, noted as future work in implementation-plan.md §7.

## Open Items (require PM resolution before implementation closes)

These items are deliberate gaps in the USDL spec drafts (`docs/tasks/fix-runner-inbox/spec/`), documented in `CHANGES.md`. They do not block implementation but must be resolved before the spec draft files are promoted to `docs/usdl/`:

1. **`ifc.inbox-ack` mode mismatch.** `top.com.runner-to-postgres-inbox` carries both `ifc.inbox-consume` (`mode=stream-pull`) and `ifc.inbox-ack` (`mode=sync-request-response`). Spec-lint will flag this. Resolution options: (a) split into two communication edges, or (b) remove `ifc.inbox-ack` from `carries=`. Option (b) is simpler. **Decision needed from PM/architect before spec promotion.**

2. **`com.flow.runner-inbox-consume` is an orphan call-flow.** No operation's `governed-by` points at it (impossible: the contract provider is `top.postgres`, which cannot host call-flows under spec-lint rules). Resolution options: (a) accept as documentation-only, or (b) remove it. **Decision needed before spec promotion.**

3. **`behavior.xml` not updated.** The implementation plan explicitly chose not to update `behavior.xml` (no user-visible behavior change). However, the three new guarantees introduced by the acceptance scenarios — pending message survives eviction (AC2), pending message survives backend restart (AC3), p95 wake-up ≤ 50 ms (AC4) — are worth capturing as `<invariant>` and `<nfr>` blocks under `beh.ask-assistant` in a follow-up. Not a blocker for this task.
