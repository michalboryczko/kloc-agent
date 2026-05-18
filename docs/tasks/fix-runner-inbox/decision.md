# Decision

## Chosen

**Option E — PGMQ (Postgres queue extension) for the inbox channel.**

Per-session queue stored as Postgres rows. Backend produces via
`pgmq.send` paired with `NOTIFY`; runner consumes via `LISTEN` +
`pgmq.read` + `pgmq.delete` (ack).

Outbound (runner → backend events) stays exactly as today — chunked
JSONL POST. Audit log proves it is healthy.

## Rejected

- **A / B / C / D (reverse-SSE, HTTP/2 full-duplex, WebSocket, ZeroMQ)** —
  fail the primary criterion: do not address the queue-identity root
  cause. They are transport modernizations; the bug would still be
  reproducible after each.
- **F (RabbitMQ)** — solves the bug correctly, but adds a new broker
  container (~250 MB resident), a new operational surface (mgmt UI,
  Erlang VM, separate credential model), and a new runtime dependency
  on both backend and runner. Avoidable when we already operate
  Postgres at PoC scale.

## Why PGMQ specifically

The queue identity moves **off the per-spawn `RegistryEntry`** and
onto a Postgres row keyed by `session_id`. That is the only change
required to structurally eliminate the "identity drift on eviction"
class of failure. Everything else follows from that property:

- **Survives eviction.** A new runner attaching to the same session
  reads from the same `inbox_{sid}` queue. The "old inbox dropped on
  evict" path no longer exists because there is no in-memory inbox.
- **Survives backend crash.** PGMQ rows are durable in the same WAL
  that protects `messages` and `audit_log`. The Analyst's submitted
  message cannot be lost between `POST /stream` returning 200 and the
  runner draining it.
- **FIFO per session.** PGMQ `read(vt=N, qty=1)` against a queue with
  one active consumer (the runner) is in-order by construction. The
  visibility timeout `vt` plays the same role RMQ's "redeliver on
  unacked disconnect" plays.
- **Push semantics, not poll.** A `NOTIFY inbox_{sid}` paired with each
  `pgmq.send` wakes the runner's `LISTEN` immediately. No 25-second
  long-poll loop, no idle re-poll churn.
- **No new infrastructure.** Postgres is already in the compose
  stack. The change is a single extension load
  (`CREATE EXTENSION pgmq;`) and a postgres image swap to one that
  bundles the extension (`quay.io/tembo/pg16-pgmq:latest`).
- **SQL-debuggable.** Operators can inspect inbox depth, oldest
  enqueued message, last-failed delivery directly with `psql`. The
  queue rows ARE the audit trail.

## Constraints accepted with this decision

- The Postgres image swaps to a pgmq-bundled flavour (Tembo) or
  pgmq is installed via an init script against stock postgres.
  Managed-Postgres compatibility is reduced (RDS no; Supabase yes;
  Cloud SQL limited). At PoC scale we self-host, so this is fine.
- The runner gains a runtime dependency on Postgres. It already had
  `asyncpg` available transitively via the SDK; we make it explicit.
  Coupling: runner ↔ data DB. We accept this for PoC scale.
- Each running runner holds one Postgres connection in `LISTEN`
  mode. At ~50 concurrent sessions this starts to compete with the
  backend's pool; mitigation is bumping `max_connections` or
  introducing PgBouncer in *session*-mode (transaction-mode does
  not support `LISTEN`). We do **not** ship PgBouncer in this task —
  it is a follow-up if and when concurrency demands it.
- No backward compatibility on the inbox HTTP endpoint
  (`GET /internal/sessions/{id}/inbox`) — removed in the same change.

## Scope of this task

**In scope.**
- Replace inbox transport with PGMQ + `LISTEN`/`NOTIFY`.
- Delete the HTTP inbox endpoint and the per-`RegistryEntry`
  `asyncio.Queue`.
- Update `HydrationPayload` to carry pg DSN + queue name.
- Add PGMQ extension to the postgres deployment.
- Update behavior / composition / topology / interfaces specs.

**Out of scope.**
- Outbound `POST /internal/.../events` unchanged.
- HMAC audit webhooks unchanged.
- SSE delivery to the FE unchanged.
- PgBouncer / connection-pool tuning (future task if needed).
- Per-session DB credentials (single shared role for PoC).
- Migration of in-flight Sessions (none — runners are ephemeral and
  any in-flight `asyncio.Queue` content is by definition lost on
  deploy).
