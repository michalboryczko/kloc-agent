# Implementation plan

Sequential. No backward compatibility — the HTTP inbox endpoint and the
in-memory inbox `asyncio.Queue` are deleted in the same change that
introduces PGMQ.

## 0. Make pgmq available in the Postgres image

Two equally acceptable approaches:

**0a. Swap the postgres image (recommended).**
```yaml
postgres:
  image: quay.io/tembo/pg16-pgmq:latest
  environment:
    POSTGRES_DB: kloc_agent
    POSTGRES_USER: kloc
    POSTGRES_PASSWORD: changeme
  command:
    - postgres
    - -c
    - shared_preload_libraries=pgmq,pg_stat_statements
  # existing volume + healthcheck unchanged
```

**0b. Stock image + init script.**
Mount a `docker-entrypoint-initdb.d/00_pgmq.sql` that installs the
extension from a downloaded `.so`. More moving parts; prefer 0a.

Either way: a fresh `CREATE EXTENSION IF NOT EXISTS pgmq;` runs as
part of the backend boot migration (step 2 below).

## 1. Python dependencies

Backend `pyproject.toml` already has `asyncpg`. No change.

Runner `pyproject.toml` (runner shares backend's `pyproject.toml`
today — verify and split if needed): ensure `asyncpg` is in the
runtime dep set. The runner does not need `sqlalchemy`; just raw
`asyncpg.connect`.

Optional helper: `pgmq-python` (Tembo's Python client). Thin wrapper
over raw SQL; acceptable to skip and call `pgmq.send` /
`pgmq.read` / `pgmq.delete` via raw `asyncpg.fetch` to avoid the
extra dep. Decision: **skip the wrapper, call PGMQ functions
directly via raw SQL**.

## 2. Backend boot — extension + per-session queue helpers

`src/messaging/__init__.py` (empty).

`src/messaging/pgmq.py` (new):
- `async def ensure_extension(conn) -> None` — `CREATE EXTENSION IF NOT EXISTS pgmq;`
- `def inbox_queue_name(session_id: str) -> str` — returns
  `inbox_{session_id_no_dashes}`. PGMQ queue names are SQL
  identifiers; UUIDs with dashes are not valid identifiers.
- `async def ensure_inbox_queue(conn, session_id) -> str` — idempotent:
  `SELECT pgmq.create_queue($1)` (PGMQ ignores duplicates). Returns
  the queue name.
- `async def send_user_message(conn, session_id, run_id, messages) -> int`
  — `pgmq.send(queue, payload)` + `NOTIFY inbox_{slug}`. Returns
  the `msg_id`.
- `async def drop_inbox_queue(conn, session_id) -> None` —
  `SELECT pgmq.drop_queue($1)`. Called from explicit session-close.

`src/main.py` lifespan:
- On startup, open one short-lived asyncpg connection, run
  `ensure_extension`, close. (Backend's main asyncpg pool can run
  PGMQ ops on demand; no need for a dedicated connection.)

## 3. Backend producer path

`src/api/stream.py:stream_post`:
- Delete the `entry.inbox.put(...)` block.
- Replace with:
  ```python
  async with get_sessionmaker()() as session:
      await ensure_inbox_queue(session.connection(), session_id)
      await send_user_message(
          session.connection(), session_id, run_id, messages
      )
      await session.commit()
  ```
  (using the existing sessionmaker so the `NOTIFY` is in the same
  transaction as the `pgmq.send`).

`src/runner_mgmt/registry.py`:
- Delete `RegistryEntry.inbox: asyncio.Queue`.
- Delete `RunnerRegistry.inbox_get`.
- Delete the inbox field from `_install_entry`'s construction.
- `get_or_spawn` still manages container lifecycle but no longer
  creates an in-memory inbox.

## 4. Delete the HTTP inbox endpoint

`src/api/internal.py`:
- Delete `runner_inbox` (`GET /sessions/{session_id}/inbox`).
- Keep the `_PRE_RUN_BUFFER_CAP` / `_dispatch_frame` logic — those
  serve the outbound channel and are unaffected.

Tests:
- Delete `tests/integration/test_runner_inbox.py` (and any
  unit-tests asserting on `inbox_get`).
- Delete inbox-related assertions in
  `tests/runner_mgmt/test_registry.py`.

## 5. Runner consumer

`runner/inbox_consumer.py` (new):
```python
async def consume_inbox(
    pg_dsn: str,
    session_id: str,
    queue_name: str,
) -> AsyncIterator[tuple[int, dict]]:
    """Yield (msg_id, payload) tuples in FIFO order.

    Caller acks by awaiting delete_message(msg_id) after processing.
    """
    listen_channel = queue_name
    while True:
        conn = await asyncpg.connect(pg_dsn)
        try:
            wake = asyncio.Event()
            await conn.add_listener(
                listen_channel, lambda *_: wake.set()
            )
            while True:
                row = await conn.fetchrow(
                    "SELECT msg_id, message::text FROM pgmq.read($1, $2, $3)",
                    queue_name, 300, 1,   # vt=300s, qty=1
                )
                if row is not None:
                    payload = json.loads(row["message"])
                    yield row["msg_id"], payload
                    continue
                wake.clear()
                # Fallback timeout protects against missed NOTIFYs
                # across reconnects.
                try:
                    await asyncio.wait_for(wake.wait(), timeout=30.0)
                except asyncio.TimeoutError:
                    pass
        except (asyncpg.PostgresConnectionError, OSError):
            await asyncio.sleep(1.0)   # reconnect loop
        finally:
            await conn.close()

async def delete_message(pg_dsn: str, queue_name: str, msg_id: int) -> None:
    conn = await asyncpg.connect(pg_dsn)
    try:
        await conn.fetchrow(
            "SELECT pgmq.delete($1, $2)", queue_name, msg_id
        )
    finally:
        await conn.close()
```

In practice the runner should hold a single long-lived connection
for both LISTEN and PGMQ ops; the above is a sketch. Implementation
should share one connection with two prepared statements
(`pgmq.read`, `pgmq.delete`).

`runner/__main__.py`:
- Replace `async for inbound in channel.iter_inbound():` with
  `async for msg_id, payload in consume_inbox(...):`.
- `current_run_id["value"] = payload.get("run_id") or str(uuid4())`.
- After `_run_one_turn` (success or failure path), call
  `await delete_message(..., msg_id)`. Failure-without-ack is the
  PGMQ redelivery hook — desired behaviour for crash recovery.
- On `payload["type"] == "shutdown"`: ack and break.

`runner/channel.py`:
- Delete `iter_inbound`.
- Outbound `_stream_outbound` is untouched.

## 6. `HydrationPayload`

`src/db/models.py` (or wherever the pydantic `HydrationPayload` lives):
- Add `pg_dsn: str` — same DSN the backend uses (or a runner-scoped
  user later).
- Add `inbox_queue: str` — set by backend at spawn-time via
  `inbox_queue_name(session_id)`.
- Remove `backend_url` if it has no remaining consumer in `runner/`
  after the HTTP channels are gone. Verify by grep before removing.

`src/api/stream.py:_build_hydration_payload`:
- Pull `pg_dsn` from `Settings` (existing `database_url` or a new
  `runner_pg_dsn` setting if you want to scope perms separately).
- Set `inbox_queue` from `inbox_queue_name(session_id)`.

## 7. Lifecycle and cleanup

- The PGMQ queue is created lazily by the backend on first
  `POST /v1/sessions/{id}/stream`. Idempotent: subsequent calls are
  no-ops.
- On `_on_evict` of a runner: **do not drop the queue.** Pending
  messages must survive eviction — that's the whole point. The next
  runner spawn picks up where the old one left off.
- On explicit `session.close`: call `drop_inbox_queue(session_id)`.
  PGMQ also exposes archive semantics if we ever want to keep the
  rows.
- Background sweep: a daily task (out of scope here, but worth
  noting) can drop any `inbox_*` queue with no rows whose
  corresponding session is older than N days.

## 8. Spec updates

- `docs/behavior.xml` — no user-visible behavior change; the existing
  `beh.rule.pending-reply-affordance` rule still applies. No update
  needed.
- `docs/composition.xml` — update the transport block. Specifically
  the long-poll inbox description and the `inbox` queue line on
  `RegistryEntry`. Replace with: per-session PGMQ queue,
  `LISTEN/NOTIFY` wake-up, durable across runner respawn.
- `docs/topology.xml` — add a `pgmq` capability to the postgres
  node; mark the `inbox-http` arrow as removed; add an
  `inbox-pgmq` arrow from backend → postgres and from runner →
  postgres.
- `docs/interfaces.xml` — remove `inbox-poll-http`; add `inbox-pgmq`
  with the PGMQ contract (queue name = `inbox_{sid_slug}`, JSON
  payload schema = `{type, run_id, messages}`, `vt=300s`).

## 9. Tests

New:
- `tests/messaging/test_pgmq_topology.py` —
  `ensure_extension`, `ensure_inbox_queue` idempotence.
- `tests/messaging/test_inbox_producer.py` — `send_user_message` +
  read it back from the queue.
- `tests/integration/test_inbox_listen_notify.py` — `NOTIFY` fires
  the listener; runner-side reader wakes within ~ms.
- `tests/integration/test_inbox_redelivery.py` — produce a message,
  consumer reads but does not delete, consumer disconnects → next
  consumer receives the same message after `vt`. **Regression
  test #1.**
- `tests/integration/test_inbox_survives_eviction.py` — full
  end-to-end: spawn runner, enqueue a user_message, evict the
  runner mid-idle, spawn a fresh runner, assert the second runner
  processes the still-pending message. **Regression test #2 —
  exact reproduction of the reported bug.**

Delete:
- All HTTP-inbox tests (listed in step 4).

Test infrastructure:
- The existing pytest docker-compose override already starts
  postgres. Swap to the pgmq image there too.
- Or use `testcontainers` to spin a pgmq postgres per test
  session.

## 10. Sequence of commits

1. `feat(postgres): swap image to pg16-pgmq; load extension on boot`
2. `feat(messaging): pgmq queue helpers (ensure/send/drop) + NOTIFY`
3. `feat(stream): use pgmq for inbox; remove asyncio.Queue path`
4. `feat(runner): consume inbox from pgmq via LISTEN; remove iter_inbound`
5. `chore(api): delete GET /internal/sessions/{id}/inbox`
6. `chore(registry): delete RegistryEntry.inbox + inbox_get`
7. `test(inbox): redelivery + survives-eviction regression tests`
8. `docs(spec): update composition/topology/interfaces for pgmq inbox`

Each commit individually builds, tests pass.

## Estimated effort

- ~0.5 dev-day: postgres image swap + extension load + helpers.
- ~0.5 dev-day: backend producer wiring + delete inbox endpoint /
  in-memory queue.
- ~0.5 dev-day: runner consumer + delete `iter_inbound`.
- ~0.5 dev-day: regression tests (these are the load-bearing ones).
- ~0.25 dev-day: spec updates.

Total: **~2.25 dev-days**.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Postgres `max_connections` saturation under N runners | Bump from default 100 → 300; PgBouncer in session-mode if it becomes an issue. Out of scope here, document it. |
| `NOTIFY` payload lost on reconnect | Fallback poll every 30 s wakes the reader regardless. Belt-and-braces. |
| Visibility timeout too short (turn longer than `vt`) | Default `vt=300s` (5 min). If a turn legitimately exceeds 5 min, redelivery causes double-processing. Mitigation: set `vt` to upper bound of `RUNNER_HEARTBEAT_TIMEOUT_S * 10` or similar; surface as a setting. |
| Tembo-flavour image unavailable in some envs | Init-script fallback (option 0b) keeps stock postgres compatible. |
| Runner ↔ DB coupling later regretted | Acknowledged debt. Reversible by moving inbox to RMQ if/when product outgrows PoC scale. |
