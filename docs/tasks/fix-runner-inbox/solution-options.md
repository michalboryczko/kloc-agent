# Solution options

Scope: replace the **inbox** transport (backend → runner direction).
Outbound (runner → backend) chunked JSONL stream stays as-is — the
audit log proves it is healthy.

## Evaluation criteria

- **Survives runner eviction / respawn** — pending messages are not lost
  when a `RegistryEntry` is swapped.
- **Survives backend restart** — pending messages durable across crash.
- **FIFO per session** — ordering of user messages within one Session
  is guaranteed.
- **No FE-visible latency regression** — the inbox is the cold path
  (one message per user submit), so ~ms-level transport latency is
  acceptable.
- **Operational cost** — new containers, new deps, new credentials.

## Options considered

### A. Reverse-SSE on the existing `/inbox` endpoint
Keep `GET /internal/sessions/{id}/inbox`, but never return 204. Hold
the connection open; push one SSE `data:` frame per user_message.
- ✅ Smallest diff — both sides are HTTP+SSE already.
- ❌ Does not fix the root cause. The queue identity is still
  `RegistryEntry.inbox`. Identity drift on eviction still loses
  messages.
- **Verdict: not enough.**

### B. Full-duplex on the existing event POST
Collapse runner→backend events and backend→runner inbox onto one
chunked HTTP/2 stream (request body = events, response body = inbox).
- ✅ One conversation, not two.
- ✅ No new infrastructure.
- ❌ Finicky on HTTP/1.1; requires HTTP/2 plumbing.
- ❌ Still in-memory queue — same identity-drift class of bug.
- **Verdict: clean architecture, doesn't address root cause.**

### C. WebSocket
Single bidi connection per runner; events and inbox interleaved.
- ✅ FastAPI / aio-pika–free; native uvicorn support.
- ✅ Same brokerless story as today.
- ❌ Still in-memory queue on backend side. Identity drift unchanged.
- **Verdict: cleaner transport, same root cause.**

### D. ZeroMQ
Brokerless message library. Runner connects ROUTER; backend DEALER.
- ✅ Built-in queueing, FIFO, reconnect.
- ❌ Lose HTTP-level observability (no access log, no OTel auto-instrument).
- ❌ Adds a non-HTTP protocol surface for the team to operate.
- ❌ Hand-roll HMAC / auth.
- ❌ Same in-memory queue concern on the backend side unless you also
  store queue state somewhere durable.
- **Verdict: more code, fewer guarantees than a real broker.**

### E. PGMQ (Postgres queue extension)
Per-session queue stored as Postgres rows. `pgmq.send` + `NOTIFY` from
backend; runner `LISTEN` + `pgmq.read` + ack via `pgmq.delete`.
- ✅ No new container — reuse existing Postgres.
- ✅ Durable across runner eviction, backend restart, crash.
- ✅ FIFO per single-consumer queue.
- ✅ Queue rows are SQL-debuggable (`SELECT … FROM pgmq.q_inbox_…`).
- ❌ Runner gains a Postgres dependency (today it only speaks HTTP +
  MCP). New coupling: runner ↔ data DB.
- ❌ Each runner holds a long-lived `LISTEN` connection. With N
  concurrent sessions, eats N `max_connections` slots. Caps out
  around 50–100 concurrent sessions without PgBouncer in
  session-mode.
- ❌ PGMQ is a third-party extension; needs a custom postgres image
  or an init script. Limits managed-Postgres choices (RDS no, Cloud
  SQL limited, Supabase yes).
- **Verdict: viable, smallest infra delta, real connection-cap ceiling.**

### F. RabbitMQ
Per-session quorum queue with single-active-consumer + manual ack.
- ✅ Durable across eviction, restart, crash.
- ✅ FIFO via SAC + `prefetch=1` + manual ack.
- ✅ Runner stays "transport-only" — no coupling to data DB.
- ✅ Mgmt UI for debugging mid-incident.
- ✅ Scales to thousands of queues / sessions without connection-cap
  worries.
- ❌ New container (~250 MB RSS for RMQ + Erlang VM at idle).
- ❌ New runtime dep on both backend (`aio-pika`) and runner.
- **Verdict: viable, larger infra delta, clean isolation.**

## Comparative table

| Option | Survives eviction | Survives backend crash | FIFO | New infra | Runner deps grow | Connection-cap risk |
|---|---|---|---|---|---|---|
| A. reverse-SSE | ❌ | ❌ | n/a | none | none | none |
| B. HTTP/2 duplex | ❌ | ❌ | yes | none | none | none |
| C. WebSocket | ❌ | ❌ | yes | none | none | none |
| D. ZeroMQ | ❌ | ❌ | yes | none | pyzmq + libzmq | none |
| E. PGMQ | ✅ | ✅ | yes | postgres extension | asyncpg | yes |
| F. RabbitMQ | ✅ | ✅ | yes | rabbitmq container | aio-pika | no |

Only **E** and **F** address the root cause. The others are transport
upgrades that leave the queue-identity defect in place.

**E (PGMQ) is selected.** See [decision.md](./decision.md) for
rationale (no new infrastructure, durability comes free from the
existing Postgres deployment, runner ↔ DB coupling accepted at PoC
scale).

## Why "events" channel is out of scope

Putting runner-emitted AG-UI events through a broker (~1–3 ms per
message) costs measurable streaming-text smoothness compared to the
existing chunked-JSONL channel (~µs per message). 10s–100s of frames
per turn; user-visible regression for no correctness gain (audit log
proves outbound is healthy). Keep outbound as-is.
