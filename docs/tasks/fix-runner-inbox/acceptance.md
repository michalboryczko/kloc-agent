# Acceptance

The task is closed when **all** of the following hold.

## Functional — the reported symptom no longer reproduces

```gherkin
Scenario: second message is processed promptly by a warm runner
  Given a Session that has just completed a successful first turn
    And the warm runner is still alive within RUNNER_WARM_IDLE_S
  When the Analyst submits a second message
  Then the runner picks up the message within 1 second of submission
    And the Assistant's first visible content arrives within the normal
        TTFT envelope for a warm runner (no cold-spawn penalty)
    And no pending Assistant indicator remains after the reply
        finalises
```

## Functional — the bug class is structurally eliminated

```gherkin
Scenario: pending message survives a runner eviction
  Given a Session whose warm runner has been evicted (warm-idle or
        crash) while a user_message is pending in its inbox
  When a new runner is spawned for the same Session
  Then the new runner consumes the pending user_message from PGMQ
    And the message is processed exactly once across the eviction
    And the resulting Assistant reply is delivered to the FE on the
        original Session's stream
```

```gherkin
Scenario: pending message survives a backend restart
  Given the backend is restarted while a user_message is pending in
        the inbox queue for an active Session
  When the backend comes back up and a runner reconnects
  Then the runner consumes the pending message from PGMQ
    And the message is processed exactly once across the restart
```

## Performance

- **Wake-up latency.** From `pgmq.send` + `NOTIFY` on the backend to
  the runner's `consume_inbox` yielding the payload: **p95 ≤ 50 ms**
  on local docker compose. Measured by a test that sends 100
  messages and records receive-time deltas.
- **No long-poll churn.** The runner does not issue any periodic
  poll faster than the configured fallback interval (30 s). Verified
  by tcpdump or by enabling `log_statement = 'all'` on Postgres and
  inspecting query rates.

## Code structure

The following code paths **must not exist** after the change:

- `GET /internal/sessions/{session_id}/inbox` endpoint
  (`src/api/internal.py:runner_inbox`)
- `RunnerRegistry.inbox_get`
- `RegistryEntry.inbox: asyncio.Queue`
- `runner/channel.py:iter_inbound`
- `runner/channel.py` HTTP long-poll constants (`INBOX_POLL_TIMEOUT_S`
  if it has no other consumer)
- Any test that imports or asserts on the above

A grep `inbox` in `src/api/internal.py`, `src/runner_mgmt/registry.py`,
`runner/channel.py` returns no matches.

## Tests

- `tests/integration/test_inbox_redelivery.py` — passes. Asserts a
  message is redelivered to a fresh consumer after the previous
  consumer disconnects without ack.
- `tests/integration/test_inbox_survives_eviction.py` — passes.
  Exact reproduction of the reported scenario:
  1. Submit message 1; assert success.
  2. Force-evict the runner.
  3. Verify the pending message 2 is still present in PGMQ
     (`SELECT count(*) FROM pgmq.q_inbox_<slug>` = 1).
  4. Trigger a fresh spawn (new POST /stream).
  5. Assert the new runner processes message 2 and the FE receives
     the Assistant reply.
- `tests/messaging/*` — new unit tests for `ensure_extension`,
  `ensure_inbox_queue`, `send_user_message`, `delete_message`.
- Existing suite (`pytest tests/ -q`) remains green.

## Operational

- `docker compose up` brings the stack up with the pgmq-enabled
  Postgres image; backend logs `pgmq extension ready` at lifespan
  startup.
- `psql` against the running postgres can `\dx` to list pgmq and
  `SELECT * FROM pgmq.metrics_all()` to inspect queue health.
- On `session.close`, the corresponding `inbox_{sid}` queue is
  dropped (verified by `SELECT * FROM pgmq.list_queues()` before
  and after).

## Documentation

- `docs/composition.xml` reflects the new transport.
- `docs/topology.xml` reflects the pgmq capability on postgres and
  the inbox arrows.
- `docs/interfaces.xml` describes `inbox-pgmq` and no longer
  references `inbox-poll-http`.
- This task's `README.md` status is updated to "Closed".

## Out of acceptance scope

The following are explicit non-goals for this task. Their absence is
not a blocker for closing the task:

- PgBouncer / connection-pool tuning.
- Per-session DB credentials.
- Multi-worker backend horizontal scaling.
- Migration from the deleted HTTP endpoint to PGMQ (clean cut, no
  back-compat).
