# Root cause

## The transport (today)

Two HTTP channels between backend and runner, both initiated by the runner:

- **Outbound** — `POST /internal/sessions/{id}/events`. Chunked JSONL.
  One long-lived request body containing every AG-UI event the runner
  emits. Healthy.
- **Inbound** — `GET /internal/sessions/{id}/inbox?timeout_s=25`. Long-poll.
  Backend blocks up to 25 s on a per-session `asyncio.Queue`; returns
  `200 + JSON` when something is enqueued, or `204` on timeout. Runner
  re-polls on 204. **This is where the bug lives.**

The inbox queue is owned by `RegistryEntry.inbox`
(`src/runner_mgmt/registry.py:_install_entry`). One `asyncio.Queue` per
spawn of a runner container.

## What actually fails

The producer (`stream_post`) and the consumer (`inbox_get`) can end up
holding references to **different `asyncio.Queue` instances for the
same `session_id`**. The producer's `put` succeeds onto queue A; the
consumer's `get` blocks forever on queue B; the user message is
unreachable until A is garbage-collected (which happens, eventually,
on the next eviction).

The drift happens because the inbox queue identity is bound to a
`RegistryEntry` lifecycle, not to the session. Any path that swaps the
`RegistryEntry` for a session — eviction-then-respawn, crash recovery,
the spawn-lock retry — creates a window in which `stream_post` can see
one entry and `inbox_get` can see another.

## Why this is structural, not a patch-the-loop problem

The bug is not "iter_inbound got stuck" or "the long-poll timed out
wrong". Every layer is doing what it's supposed to:

- `stream_post`'s `entry.inbox.put` succeeds (no error, no log).
- `iter_inbound`'s next `GET /inbox` succeeds (no error, returns 204).
- `RunnerRegistry.inbox_get` succeeds (returns `None` because *its*
  queue is empty).

The queue *both sides reference for the same session* is not the same
object. There is no single point in the long-poll loop that could be
"fixed" — the long-poll faithfully reports the state of the queue the
backend hands it. The queue is the wrong queue.

## The class of failure

The shape of this bug — a producer and consumer keyed on the same
business identity (session) but bound through a process-local
mutable map of in-memory queues — admits at least three failure
modes:

1. **Identity drift on eviction** (this report). `_install_entry` runs
   a fresh queue; messages already enqueued on the old one are lost.
2. **Identity drift on crash recovery**. The heartbeat watcher kills
   an entry; a `get_or_spawn` race re-installs a fresh one while a
   pending `inbox.put` from a concurrent `stream_post` lands on the
   old entry it had already resolved.
3. **Identity drift on shutdown**. Backend shutdown discards all
   entries; in-flight `stream_post` callers that already resolved an
   entry can put onto a queue nothing will ever read.

The current code defends (1) and (3) with operational workarounds
(orphan sweep on boot, warm-idle ceiling). It cannot defend against
(2) because the race is in the resolve-then-put gap and the only
correct fix is to put the queue identity outside the lifecycle of
`RegistryEntry`.

## What stays sound

- Outbound `POST /internal/.../events` (runner → backend, chunked
  JSONL). Single long-lived connection, ordering preserved by the
  socket, has replay-on-break logic (`yielded_this_attempt`). The
  audit log proves this channel delivered every event correctly even
  in the failing case.
- `EventBus`, `ExecutionRegistry`, `_persist_events`. These are
  downstream of the broken channel and are correct given correct input.
- SSE delivery to the browser. Identity-keyed on `(session_id, run_id)`,
  which is also what the runner emits. When the inbox delivers the
  *intended* `run_id` to the runner, the SSE topic matches.

## The fix has one job

Move the inbox queue identity off the per-spawn `RegistryEntry` and
onto something that survives runner replacement and is keyed by
`session_id` alone. That means a **transport with an external
addressable queue**, not an in-memory `asyncio.Queue`.

This is a transport-layer change, not a bug fix.
