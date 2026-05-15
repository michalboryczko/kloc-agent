# Test Failure → Code Review Findings Mapping

Maps the 6 reported test failures to verified code review findings that are likely
root causes or contributors. Only findings with a plausible causal link to the
reported failures are listed. Findings that were refuted or unrelated have been
omitted.

Verification status of each cited finding: see `kloc-agent` review session.
`confirmed` = code matches the described defect; `partial` = real but narrower
than originally claimed.

---

## Issue 1 — Concurrent session cross-talk (scenario 11)

**Symptom**: Under parallel sessions, at least one returns empty assistant
content. `assert token in assistant_content` fails with `got: ''`.

**Likely contributors**:

- **`src/api/stream.py:91-98` — `_persist_events` fire-and-forget task swallows
  exceptions** *(confirmed)*
  - `asyncio.create_task(_persist_events(...))` is not stored and has no done
    callback. Any exception in the persist task disappears into the asyncio
    default exception handler.
  - Under parallel load this is the most plausible reason a single session's
    assistant content lands empty in the DB while others succeed: one task
    raises, the rest don't.

- **`src/api/stream.py` `_persist_events` opens a new `AsyncSession` per delta**
  *(confirmed, formerly listed as a `get_sessionmaker()` issue)*
  - Each TEXT_MESSAGE_CONTENT event checks out a fresh connection. Under N
    concurrent sessions this saturates the pool; checkout timeouts surface as
    persist failures, which are then swallowed by the fire-and-forget pattern
    above.

- **`src/repos/messages.py:29-35` — `_next_seq` race on concurrent inserts**
  *(confirmed in prior review)*
  - `SELECT max(seq)+1` then INSERT is not atomic within a transaction. Two
    concurrent inserts for the same `session_id` can read the same max and the
    second INSERT trips `uq_messages_session_seq`. Combined with the swallowed
    exception above, the row never persists and content is empty.

**Why these together explain the failure**: a UNIQUE-violation or pool-checkout
exception during streaming → swallowed by the fire-and-forget task → assistant
row never gets the delta → empty content at assertion time.

---

## Issue 2 — Resume / cursor-replay regression (scenario 5)

**Symptom**: Passed in prior run, now FAIL. Backend stderr shows
`starlette.requests.ClientDisconnect at src/api/internal.py:165
(ingest_runner_events)`.

**Likely contributors**:

- **`src/api/internal.py:114-122` — Orphan event detection silently drops
  pre-RUN_STARTED frames** *(confirmed)*
  - If `run_id` is `None` (no `RUN_STARTED` cached yet), frames are logged and
    dropped. On a resume path, the cached run id can be missing while replay
    frames are already arriving — they are silently lost.

- **`runner/channel.py:123-172` — Outbound runner→backend stream has no
  reconnect** *(confirmed in prior review)*
  - On any exception in the chunked POST, `_stream_outbound` logs and returns.
    A single transient backend close permanently silences this runner's event
    stream, surfacing as `ClientDisconnect` on the backend side at
    `internal.py:165`.

**Why this explains the regression**: resume re-establishes the channel; if
frames arrive before `RUN_STARTED` is cached or if the channel hiccups during
replay, events are lost with no recovery. The `ClientDisconnect` in the
traceback is the symptom, not the cause.

---

## Issue 3 — Warm-idle eviction + respawn (scenario 3)

**Symptom**: Previously timed out, now FAIL with adequate budget. Eviction
window assertion or post-eviction respawn is misbehaving.

**Likely contributors**:

- **`src/runner_mgmt/registry.py:150-165` — Race after `kill_in_flight`:
  duplicate spawn window** *(partial — narrower than originally claimed)*
  - Between `await_kill_in_flight` and the re-fetch/spawn, a second concurrent
    `get_or_spawn` for the same session can also see `entry is None`. The
    second spawn overwrites the entry under `_lock`, leaking an orphaned
    container rather than crashing.
  - Eviction-then-respawn is exactly the path that creates this stale window,
    matching the scenario's setup.

- **`src/api/webhooks.py:259-294` — HMAC fallback secret for pruned runners**
  *(confirmed)*
  - `_resolve_runner_secret` returns the global bootstrap secret with
    `secret_source="fallback"` when the registry entry is missing. After
    eviction, the respawned runner's tool calls may authenticate via fallback
    — assertions that check the secret source or audit log would fail.

- **`src/runner_mgmt/sweeper.py:74-76` — `container.stop` exceptions swallowed
  with `pass`** *(confirmed, severity Low)*
  - Bare `except: pass` on stop, then proceeds to delete. An orphan from the
    race above might not be cleaned up cleanly between eviction and respawn,
    leaving state for the next assertion to trip on.

---

## Issue 4 — Vertical-slice RUN_FINISHED missing (scenario 1)

**Symptom**: `assert_run_completed(events)` trips — RUN_FINISHED frame not
reaching the test before assertion.

**Likely contributors**:

- **`src/api/stream.py:91-98` — `_persist_events` fire-and-forget task swallows
  exceptions** *(confirmed)*
  - If `_persist_events` dies before processing RUN_FINISHED, the terminal
    frame can be lost from the persistence side without any error surfacing.

- **`src/streaming/event_bus.py:24` — `put_nowait` can silently drop events
  when subscriber queue is full** *(confirmed in prior review)*
  - `asyncio.QueueFull` is swallowed. A slow SSE subscriber can miss the
    RUN_FINISHED frame entirely. Maxsize is 10 000 but heavy delta traffic
    plus a slow client can plausibly fill it.

---

## Issue 5 — Rehydrate / same_chat (scenario 4)

**Symptom**: Session-ID equivalence or "no seam in assistant response"
assertion. Tied to the `src/api/sessions.py` rehydrate path.

**Likely contributors**:

- **`src/api/stream.py:265-332` — `_build_hydration_payload` loads up to
  `limit=10_000` messages with no truncation** *(confirmed in prior review)*
  - Large rehydrate produces an enormous prompt; combined with per-delta
    `AsyncSession` opens during the subsequent stream, the pool is under
    pressure exactly when the response begins, producing a visible seam.

- **`src/api/stream.py` per-delta `AsyncSession`** *(confirmed; same finding
  cited in Issue 1)*
  - Connection-checkout latency during early deltas plausibly explains a seam
    in the assistant response after rehydrate.

Note: this is the weakest mapping — calibration or a genuine equality assertion
bug in `sessions.py` is also plausible. Needs the traceback to confirm.

---

## Issue 6 — Backend ClientDisconnect noise

**Symptom**: Repeated `starlette.requests.ClientDisconnect at
src/api/internal.py:165 in ingest_runner_events`. Cross-cuts several FAILs.

**Likely contributors**:

- **`runner/channel.py:123-172` — Outbound runner→backend stream has no
  reconnect** *(confirmed in prior review)*
  - The runner side closes its end on any error and never retries. From the
    backend's view this looks like a client disconnect mid-stream.

- **`src/api/internal.py:114-122` — Orphan event detection silently drops
  pre-RUN_STARTED frames** *(confirmed)*
  - Same file, same handler. The drop path may be where the disconnect-handling
    is missing (no try/except around the body iterator that tolerates
    `ClientDisconnect` gracefully).

**Action**: backend `ingest_runner_events` should wrap the body iteration in
`try/except ClientDisconnect` and log at info, not let it bubble. Independently,
the runner side needs a reconnect loop so a single transient disconnect doesn't
silence the channel for the rest of the session.

---

## Shared root cause across Issues 1, 2, 4, 6

The combination of:

1. `_persist_events` swallowing exceptions (`src/api/stream.py:91-98`)
2. Per-delta `AsyncSession` checkout in `_persist_events`
3. Runner outbound stream with no reconnect (`runner/channel.py:123-172`)

…explains the cluster of "empty content / missing terminal frame /
ClientDisconnect noise" failures. Patching #1 first will surface the
underlying tracebacks that disambiguate the rest.

## Recommended fix order

1. **`src/api/stream.py:91-98`** — store the task; add a done callback that
   logs `task.exception()`. Lowest-risk change, immediately recovers
   diagnostic signal across Issues 1, 4, and parts of 2 and 6.
2. **`src/api/internal.py:114-122`** — buffer pre-RUN_STARTED frames and
   flush on first RUN_STARTED. Addresses Issue 2 directly and likely
   parts of Issue 6.
3. **`src/runner_mgmt/registry.py:150-165`** — per-session lock around
   the check-then-spawn sequence. Addresses Issue 3.
4. **`src/api/webhooks.py:259-294`** — reject `secret_source == "fallback"`
   when an env flag is set (default on outside dev). Addresses Issue 3.
5. **`runner/channel.py:123-172`** — reconnect loop with backoff.
   Addresses Issue 6 and reduces flake across the suite.
6. **`src/api/stream.py` `_persist_events` session lifetime** — open one
   `AsyncSession` for the duration of the persist coroutine. Addresses
   pool pressure cited in Issues 1 and 5.

## Not mapped to a finding (needs traceback)

- Issue 5 may be a calibration issue or a genuine equality bug in
  `src/api/sessions.py`. The rehydrate-related findings above are
  plausible but not strong.
