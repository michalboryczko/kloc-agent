# Problem — "second message does nothing"

## Symptom (user-visible)

After a successful first turn in a Session, the second message submitted
by the Analyst shows the pending Assistant indicator (avatar + name +
animated dots) but no reply ever arrives in the conversation surface.
Waiting 3+ minutes does not unblock it; the spinner stays.

Refreshing the Session sometimes reveals that the Assistant's response
*was actually produced* and persisted to the database — the Analyst's
view simply never received it.

Screenshot reference: stuck on `kloc analyst` with three pulse dots,
no error banner, no retry button.

## Reproduction

1. Open a fresh Session (`POST /v1/sessions` then navigate to it).
2. Send a first short prompt (e.g. "hi"). Observe the Assistant reply
   streams normally and the Session goes idle.
3. Wait less than `RUNNER_WARM_IDLE_S` (current 300 s) — the warm
   runner container stays alive.
4. Send a second prompt (e.g. "list flows"). The pending indicator
   appears; nothing follows.

Reproduces 100% of the time when the second submit lands during the
warm-idle window after the first turn completed.

## Evidence — DB row order (session `dc1d6a4f`)

```
seq | role      | content                                            | created_at
  1 | user      | "hi"                                               | 21:59:50
  2 | assistant | "Hello! I am your code-intelligence research…"    | 21:59:57
  3 | user      | "list flows"                                       | 22:00:07  ← submitted ~10s after turn 1
  4 | user      | "?"                                                | 22:04:19  ← submitted while turn-2 stuck
  5 | assistant | "It looks like you're asking for help…"            | 22:05:13
  6 | assistant | "Here are the application flows found in the…"   | 22:05:22
```

The Analyst's `list flows` (seq 3) was persisted to the DB at 22:00:07,
but the corresponding Assistant reply (seq 6) was not produced until
22:05:22 — **5 minutes and 15 seconds later**.

## Evidence — audit log for the same session

```
event_type                | created_at                | notes
session_opened            | 21:59:49                  |
runner_spawned            | 21:59:50  runner=7db8ba73 | for "hi"
message_persisted         | 21:59:57                  | "hi" reply finalized
                          |  …silence for 5 minutes…  |
runner_warm_idle_evicted  | 22:05:02  runner=7db8ba73 | warm-idle 300 s elapsed
runner_spawned            | 22:05:03  runner=43f08fa6 | new runner
message_persisted         | 22:05:17                  | "?" reply finalized
tool_call.started         | 22:05:19  tool=kloc_flows |
tool_call.completed       | 22:05:19  tool=kloc_flows |
message_persisted         | 22:05:23                  | "list flows" reply finalized
```

The old runner (`7db8ba73`) is the **same runner instance** that
successfully answered "hi". It then sat idle for five minutes, ignoring
the `list flows` and `?` messages that had been submitted to its
inbox, until warm-idle eviction killed it.

A brand-new runner (`43f08fa6`) spawned by the next `POST /stream` and
drained the backlog within ~20 seconds.

## Evidence — runner inbox

The backend writes user messages to a per-`RegistryEntry` in-memory
`asyncio.Queue` (`src/runner_mgmt/registry.py:_install_entry`). The
runner long-polls `GET /internal/sessions/{id}/inbox`
(`runner/channel.py:iter_inbound`).

For the failing turn:

- `stream_post` ran `await entry.inbox.put(user_message)` and returned
  HTTP 200.
- The runner's `iter_inbound()` loop was not blocked on an HTTP call —
  the dev-log shows it was issuing `GET /inbox` repeatedly and
  receiving `204 No Content`.
- Therefore the `entry.inbox.put` and the `entry.inbox.get` were
  **operating on different queue objects** for the same session.

This is the bug.

## Evidence — FE experience

The Frontend submitted with FE-generated `run_id = rA` and registered
an SSE subscription on the EventBus topic `(sid, rA)`. The eventual
new-runner turn emitted AG-UI events tagged with `run_id = rB` (a
backend-fallback UUID, since the inbox message it consumed had a
different `run_id` than the FE expected). Those events were published
to bus topic `(sid, rB)`. The FE's subscription on `(sid, rA)` received
nothing; the persister's subscription (registered separately on
`(sid, rB)`) received everything and wrote it to the DB.

That is why the database has the Assistant reply but the conversation
surface does not.

## Why the workaround (`RUNNER_WARM_IDLE_S = 60 → 300`) did not fix it

Earlier we bumped `RUNNER_WARM_IDLE_S` from 60 s to 300 s on the
assumption that a 25-second cold-start penalty per turn was the
problem. It is not. The eviction was not the root cause; it was the
**rescue mechanism** that eventually allowed a fresh runner to drain
the lost messages. Increasing the warm-idle window only delays when
the rescue happens.

## Severity

- Functional: the product appears to break after any successful turn.
- Reputational: the Analyst loses trust in the assistant's reliability.
- Data: no message loss in the database (turns DO complete and persist),
  but the user-visible conversation diverges from the durable state.

## Constraints on the fix

- No backward compatibility on the runner ↔ backend transport. Current
  endpoints that exist only to serve this channel
  (`POST /internal/sessions/{id}/events`,
  `GET /internal/sessions/{id}/inbox`) are removable.
- HMAC audit webhooks (`/v1/webhooks/runners/{id}/events`) are a
  separate concern and must remain.
- SSE delivery to the browser (`POST /v1/sessions/{id}/stream`,
  `GET /v1/sessions/{id}/stream`) is unaffected and must remain.
