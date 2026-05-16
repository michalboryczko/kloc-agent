---
phase: 01-backend-ag-ui-runner-correctness
reviewed: 2026-05-16T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - src/api/internal.py
  - src/api/stream.py
  - runner/channel.py
  - runner/hooks/audit.py
  - tests/unit/test_internal.py
  - tests/unit/test_stream.py
  - tests/unit/test_audit_hook_drain.py
  - tests/unit/test_channel.py
  - tests/integration/test_stream_reconnect.py
findings:
  critical: 2
  warning: 8
  info: 4
  total: 14
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-05-16
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

The phase fixed five named regressions (ISS-01 pre-RUN_STARTED buffer flush ordering, ISS-02 persister dedup, ISS-03 AfterToolCall drain on stop, ISS-04 stale RUN_FINISHED CAS guard, ISS-06 in-flight frame replay on reconnect). The narrow regression fixes are sound, but adversarial tracing surfaces two new correctness bugs introduced or untouched by this phase, several resource-leak windows, and material test-quality gaps that mean some of the "regression" tests would pass against the pre-fix code or fail to exercise the invariant they claim to.

The two BLOCKER findings are:
1. `stream_get` (`/v1/sessions/{id}/stream` GET reconnect path) has a replay/live-tail seam that silently drops events emitted between the `replay_from` snapshot and the `event_bus.subscribe` registration — the same class of bug the rest of the phase was hardening against, on the *other* SSE entrypoint.
2. `runner/channel.py` `_stream_outbound` recovery on a `4xx`/`5xx` response loses every frame body_iter yielded during the failed attempt except the most recent one (`last_inflight`). The exception-path branch correctly drains the outbound queue and prepends `last_inflight`, but the `>= 400`-status branch only prepends `last_inflight` — frames yielded earlier in that same attempt are gone.

## Critical Issues

### CR-01: `stream_get` replay→live seam drops events on reconnect

**File:** `src/api/stream.py:150-161`, `src/api/stream.py:164-171`
**Issue:** The GET reconnect handler replays from the execution ring, then subscribes to the live event bus. Between `execution.replay_from(last_event_id)` (snapshot of `events` deque at call time) and `event_bus.subscribe(...)` (which awaits `event_bus.register` and adds a queue to `_subs`), new events can land on `execution.events` (via the persister) but the new subscriber's queue does not yet exist in `_subs`, so `event_bus.publish` skips it. Worse, `_live_stream` accepts `last_seq` but never uses it — there is no `replay_from(last_seq)` second pass after the subscriber is registered. Any event with `seq > last_event_id` that arrived in this window is silently lost from the resumed client's view.

This is exactly the same family of race that `event_bus.register` + `consume` was designed to close on the POST path (`stream_post` registers BEFORE telling the runner to start). The GET path does not use that pattern.

**Fix:**
```python
async def stream_get(...):
    if run_id is None:
        raise HTTPException(status_code=400, detail="run_id required")
    execution = await execution_registry.get(session_id, run_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="unknown execution")

    # Register the live subscriber BEFORE snapshotting the ring so any
    # event that lands during the gap is queued onto our subscriber.
    queue = await event_bus.register(session_id, run_id)
    try:
        async def generator():
            highest_seq = last_event_id
            for entry_dict in execution.replay_from(last_event_id):
                highest_seq = entry_dict["seq"]
                yield entry_dict["event"]
            if execution.status != "running":
                return
            # Second-pass replay: anything appended while we were
            # registering goes through the ring, not the bus queue.
            for entry_dict in execution.replay_from(highest_seq):
                highest_seq = entry_dict["seq"]
                yield entry_dict["event"]
            async for event in event_bus.consume(session_id, run_id, queue):
                yield event
                if is_run_lifecycle_terminal(event):
                    return
        return make_response(request, generator())
    except BaseException:
        # consume() finally-block won't run unless the generator iterates;
        # discard the queue from _subs explicitly on early exit.
        async with event_bus._lock:  # or expose a public unregister()
            bucket = event_bus._subs.get((session_id, run_id))
            if bucket is not None:
                bucket.discard(queue)
        raise
```
There is no regression test for `stream_get` reconnect at all in the listed test files, so the bug is not surfaced by the current suite.

---

### CR-02: `runner/channel.py` 4xx/5xx branch drops every frame except `last_inflight`

**File:** `runner/channel.py:197-211`
**Issue:** On `response.status_code >= 400`, the recovery code does:
```python
if last_inflight is not None:
    pending_after_break.append(last_inflight)
    last_inflight = None
await asyncio.sleep(backoff)
backoff = min(backoff * 2, max_backoff)
continue
```
That preserves only the most recent frame. The exception branch at line 217+ correctly does both `pending_after_break.append(last_inflight)` AND drains `self._outbound` into `pending_after_break`. The 4xx branch does NOT drain the queue, but the body_iter generator from this attempt was already pulling frames off `_outbound` and yielding them to httpx — those frames are gone from the queue, gone from the wire, and not in `pending_after_break`. On the next attempt, `body_iter()` is re-invoked from scratch; it will only see whatever `_outbound` still holds.

Consequence: a single 4xx (e.g., transient backend startup race that triggers a 503, or a bad-routing 404 during a backend redeploy) silently loses all events that body_iter yielded during the failed attempt, including potentially `RUN_FINISHED` if it was already queued. The very class of loss ISS-06 was meant to prevent.

Note also that on `4xx/5xx` the body iterator is still suspended inside the `async with` context manager — when control exits the `async with`, the iterator is closed (`GeneratorExit`). This makes the loss happen at GC time, not at the `continue`, but the result is the same.

**Fix:** Apply the same drain+prepend pattern as the exception branch:
```python
if response.status_code >= 400:
    log.error("channel.outbound_bad_status", extra={"status": response.status_code})
    if last_inflight is not None:
        pending_after_break.append(last_inflight)
        last_inflight = None
    # NEW: also drain anything body_iter pulled-but-may-have-flushed,
    # plus anything still queued. We can't recover yielded-but-lost
    # frames if pending_after_break.clear() already ran in this attempt
    # (see WR-04), but at minimum drain whatever is left in _outbound.
    while True:
        try:
            event = self._outbound.get_nowait()
        except asyncio.QueueEmpty:
            break
        if event is None:
            sentinel_seen = True
            break
        pending_after_break.append(event)
    if sentinel_seen:
        return
    await asyncio.sleep(backoff)
    backoff = min(backoff * 2, max_backoff)
    continue
```

There is no test for the 4xx path in `test_channel.py`. The two reconnect tests (`test_reconnect_replays_last_inflight_frame`, `test_reconnect_preserves_order_when_inflight_and_queue_both_present`) only exercise the `RemoteProtocolError` (exception) path.

---

## Warnings

### WR-01: `stream_post` registers a bus queue that leaks on every error between `register` and `consume`

**File:** `src/api/stream.py:85-133`
**Issue:** `event_bus.register(session_id, run_id)` inserts a queue into `_subs`. The cleanup lives in `event_bus.consume`'s `finally` block, which only runs if the generator returned by `generator()` is actually iterated. Anything that raises between the `register` call (line 85) and the moment the StreamingResponse begins consuming (`make_response` returning + framework iterating) leaks that queue forever. Concrete leak vectors:

- `entry.inbox.put(...)` (line 88) — the entry.inbox `asyncio.Queue` could be full or `entry` could have been concurrently terminated.
- `execution_registry.get_or_create` raising under contention or memory pressure.
- `_build_hydration_payload` raising (already runs BEFORE register, but if it's reordered).
- The client disconnects between request acceptance and first iteration of the StreamingResponse body.

Every leaked queue stays in `_subs` forever and `event_bus.publish` will keep `put_nowait`'ing into it until it hits 10 000 capacity, at which point the slow-subscriber path drops the *other* legitimate subscribers via sentinel.

**Fix:** Wrap the body of `stream_post` between `register` and the `return make_response(...)` in a try/except that discards the queue from `_subs` on failure. Cleanest is to expose `event_bus.unregister(session_id, run_id, queue)` and call it from an `except BaseException`.

---

### WR-02: `_persist_events` holds an asyncpg connection for the lifetime of a possibly-stuck run

**File:** `src/api/stream.py:194-258`
**Issue:** `_persist_events` opens an `AsyncSession` and holds it open for the entire run (one-session-per-run was an intentional pool-pressure fix per the docstring). However it returns only on `is_run_lifecycle_terminal` (RUN_FINISHED / RUN_ERROR). If the runner crashes without emitting a terminal frame, the persister never returns: the AsyncSession is checked out indefinitely, and the bus subscription stays in `_subs` (until the runner heartbeat watcher kills the run, but that path emits no synthetic RUN_ERROR onto this run's bus topic).

The phase explicitly fixed the per-delta connection-churn case in the other direction. The leak vector under runner crash + missing synthetic RUN_ERROR is left open.

**Fix:** Pair the loop with the heartbeat lost path: when `RunnerRegistry.on_heartbeat_lost(session_id)` fires, the backend should publish a synthetic `RUN_ERROR` onto the bus for the active run on that session so all subscribers (persister included) drain and clean up. Alternatively, add a timeout-driven escape inside `_persist_events` (e.g., `asyncio.wait_for(event_bus.subscribe(...), heartbeat_timeout_s + grace)` per-iteration).

---

### WR-03: `_persist_user_message` runs before runner-registry health check; orphan user rows on 503

**File:** `src/api/stream.py:69-71`
**Issue:** The flow is: persist user message → commit → `_get_runner_registry` (which raises 503 if registry is None) → spawn runner. If the registry is unavailable (lifespan boot failure), the message is durably persisted but the client gets a 503 and the runner never runs. On retry the message will be replayed as part of `prior_messages` and the user will see their own message duplicated in hydration.

Contract A invariant #1 says "persist user message FIRST" — but that ordering assumes the rest of the pipeline can proceed. Under registry unavailability we get a half-committed transaction (the user side persisted, the runner side never started).

**Fix:** Check `_get_runner_registry(request)` BEFORE `_persist_user_message`, OR persist the message inside a savepoint that only commits after `get_or_spawn` succeeds. Order:
```python
registry = _get_runner_registry(request)   # fail fast on 503 before commit
await _persist_user_message(session_uuid, messages)
hydration_payload = await _build_hydration_payload(...)
entry = await registry.get_or_spawn(session_id, hydration_payload)
```

---

### WR-04: `runner/channel.py` body_iter replay produces duplicate frames if a reconnect happens during replay

**File:** `runner/channel.py:153-173`
**Issue:** `body_iter` first replays `pending_after_break`, then clears it (line 160), then enters the live loop. If the transport raises between yields *during the replay loop* (e.g., on the 3rd of 10 pending events), `pending_after_break.clear()` was not reached, so `pending_after_break` still contains items [0..N]. The exception handler then appends `last_inflight` (= the most recently yielded item, say index 2) onto the existing list and drains the queue.

Result on next attempt's replay: `pending_after_break` = [original_0, original_1, original_2, ..., original_N, last_inflight=original_2, ...queue_drainage]. The first three items get re-sent, then they appear again (original_2 a third time, original_0/1 a second time). Duplicates beyond just the in-flight frame.

The phase notes "Over-delivery is preferable to silent loss; the backend tolerates duplicate AG-UI intermediates." (line 149) — but duplicating RUN_STARTED/RUN_FINISHED across the bus could trigger the very mapping-wipe scenarios ISS-04 is guarding against, depending on timing.

**Fix:** Move `pending_after_break.clear()` to happen incrementally as each pending item is consumed:
```python
async def body_iter():
    nonlocal last_inflight
    while pending_after_break:
        event = pending_after_break[0]
        last_inflight = event
        yield (json.dumps(event) + "\n").encode("utf-8")
        pending_after_break.pop(0)   # only pop AFTER successful yield
    while True:
        event = await self._outbound.get()
        if event is None:
            return
        last_inflight = event
        yield (json.dumps(event) + "\n").encode("utf-8")
```

---

### WR-05: `AuditHookSender.stop()` drain-vs-cancel race can still lose one tool_call.completed row

**File:** `runner/hooks/audit.py:70-95`
**Issue:** The drain loop uses `get_nowait()` until the queue is empty, then cancels the worker. But the worker `_after_loop` runs concurrently and may already be awaiting `self._post(payload, ...)` on a payload it grabbed *before* the drain started. When `self._after_worker.cancel()` fires, that in-flight POST is cancelled mid-await. The corresponding `tool_call.completed` row is lost — exactly the ISS-03 failure mode that this fix was meant to close.

The probability is small (the worker has to win the race for `await self._after_queue.get()` over the drain's `get_nowait`) but the regression test does not assert against it.

**Fix:** Cancel the worker FIRST, then drain. After cancel, the worker's `_post` either completed (row persisted) or was cancelled mid-flight (row already lost); the drain then handles whatever is still queued. Symmetric:
```python
async def stop(self) -> None:
    # 1. Cancel the worker so it stops competing for queue items.
    if self._after_worker:
        self._after_worker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._after_worker
    # 2. Drain anything still queued under our exclusive ownership.
    if self._http is not None:
        while True:
            try:
                payload = self._after_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await self._post(payload, "AfterToolCall")
            except Exception:
                log.exception("audit.after_post_failed_during_drain")
    # 3. Close http.
    if self._http:
        await self._http.aclose()
        self._http = None
```

---

### WR-06: `AuditHookSender.stop()` `except (asyncio.CancelledError, BaseException)` swallows KeyboardInterrupt and SystemExit

**File:** `runner/hooks/audit.py:91`, `runner/channel.py:62`
**Issue:** Both `stop()` methods catch `BaseException` while awaiting the cancelled task. `BaseException` is the superclass of `CancelledError`, `KeyboardInterrupt`, and `SystemExit`. Catching it makes a `Ctrl-C` during shutdown silently absorbed. The `(asyncio.CancelledError, BaseException)` tuple is also redundant — `BaseException` already covers `CancelledError`.

**Fix:**
```python
try:
    await task
except asyncio.CancelledError:
    pass
```
Let `KeyboardInterrupt`/`SystemExit` propagate.

---

### WR-07: `event_bus.publish` uses `put_nowait` with a single producer; slow-subscriber sentinel can wipe healthy subscribers' queue order

**File:** `src/streaming/event_bus.py:23-55` (referenced from `src/api/internal.py:150`)
**Issue:** When publishing to a topic with multiple subscribers and one is slow, the slow subscriber's queue saturates at 10 000 and gets a sentinel; the healthy subscribers continue to receive. But: between the `for q in queues: q.put_nowait(event)` loop and the cleanup `for q in full: bucket.discard(q)` block, the lock is *released* (`async with self._lock:` on line 49 is a separate acquisition). Another `publish` can race and observe the now-saturated queue in the snapshot, causing duplicate sentinels.

More material: when a subscriber's queue is sentinel'd, its iterator in `consume` exits cleanly — but the persister-side subscriber and the user-SSE-side subscriber are both treated the same. If the *persister's* queue saturates (because pool/DB stalled), the persister gets sentinel'd and stops persisting halfway through a run. User-side SSE delivery is unaffected, but the DB is now divergent from what the user sees.

**Fix:** Distinguish "essential" (persister) vs "best-effort" (SSE) subscribers at register time; never sentinel-drop the persister. Either give the persister an unbounded queue or apply backpressure on publish (`await q.put(...)` with timeout) instead of dropping.

---

### WR-08: `_dispatch_frame` writes diagnostic frames to stderr unconditionally even outside dev/CI

**File:** `src/api/internal.py:36-41`, used throughout
**Issue:** `_diag()` is `print(msg, file=sys.stderr, flush=True)` — a sync I/O call inside an async handler, on the hot path of every JSONL frame ingress (one per AG-UI event). At even moderate runner throughput this both blocks the event loop on every frame and floods stderr. The docstring justifies this for diagnosing uvicorn log-config filtering, but no env-gate exists to disable it.

**Fix:** Gate behind `if log.isEnabledFor(logging.DEBUG):` or `if os.environ.get("KLOC_DIAG_EVENTS")`. The existing `log` logger should be used; the uvicorn-filter problem the comment cites should be fixed in the log config rather than worked around per-frame.

---

## Info

### IN-01: Tests in `test_audit_hook_drain.py` do not exercise the ordering claim they make

**File:** `tests/unit/test_audit_hook_drain.py:60-83`
**Issue:** `test_stop_drains_after_queue_before_cancel` installs a pre-completed worker (`asyncio.create_task(asyncio.sleep(0))`), so `done()` is True before `stop()` is even called. The test does NOT verify that drain happens *before* worker cancel — it only verifies that drain happens. Against pre-fix code where the worker was cancelled first and then... wait, pre-fix code never drained at all, so the test does still distinguish pre-fix from post-fix. But the test's *name* and docstring claim ordering, which it doesn't actually exercise.

**Fix:** Add an explicit ordering test: install a worker that is currently mid-await (e.g., on `await self._after_queue.get()`), put items in the queue, call `stop()`, assert ordering via timestamps or a shared list captured by both `_fake_post` and the worker. Verify drain wins the race.

---

### IN-02: `test_channel.py` patches `_channel_mod.asyncio.sleep` — mutates the global asyncio module

**File:** `tests/unit/test_channel.py:206-212`, `tests/unit/test_channel.py:283-289`
**Issue:** The pattern `_channel_mod.asyncio.sleep = _instant_sleep` does NOT just rebind the channel module's reference — `_channel_mod.asyncio` IS the global `asyncio` module object, so this mutates `asyncio.sleep` process-wide. Restoration in the `finally` works only if no exception/assertion failure short-circuits before reaching the `finally`. Any failure during the test (e.g., assertion error, KeyboardInterrupt) before the finally leaves `asyncio.sleep` patched for every subsequent test in the same process.

**Fix:** Use pytest's `monkeypatch` fixture, which auto-restores on test teardown:
```python
async def test_reconnect_replays_last_inflight_frame(monkeypatch):
    monkeypatch.setattr("runner.channel.asyncio.sleep", _instant_sleep)
    ...
```
Or use `unittest.mock.patch.object` as a context manager.

---

### IN-03: `_dispatch_frame` uses module-level constants defined AFTER the function

**File:** `src/api/internal.py:129, 192`
**Issue:** `_PRE_RUN_BUFFER_CAP` is referenced inside `_dispatch_frame` at line 129 but defined at line 192. This works at runtime (Python resolves module globals at call time), but is confusing to read top-to-bottom and risks NameError if `_dispatch_frame` is ever called at import time (e.g., from a module-level decorator).

**Fix:** Hoist `MAX_LINE_BYTES` and `_PRE_RUN_BUFFER_CAP` above `_dispatch_frame`.

---

### IN-04: Multiple B-DIAG diagnostic log strings have stale phase/task references

**File:** `src/api/internal.py:60-69, 99-104, etc.`; `runner/channel.py:78-83, 168-172`; `runner/hooks/audit.py:100-105, 144-149, 201-206`
**Issue:** Project comment policy (CLAUDE.md, ISS-13) is "never name people, plan sections, ACs, review rounds, or describe history". The reviewed files retain markers like `B-DIAG-EVENTS`, `B-DIAG-A`, `B-DIAG-B`, `(AC15)`, `Phase 1.A7`, `dev-2 CR`, `Track H`, `Plan §454-§477`, `ISS-01`, `ISS-04`, `ISS-06`, `Reviewer-2 C1`, `AC10`, `AC12`, `AC19`, `B-INFRA-DISPATCH`, `B-INFRA-SSE` throughout the modules and tests. These are pervasive and explicitly listed in the ISS-13 cleanup scope. If ISS-13 is supposed to land separately, the new code added in this phase should at minimum not *add new* B-DIAG-X / plan-section markers.

**Fix:** During the ISS-13 sweep, strip every `B-DIAG-*` log marker, `Plan §NNN-§NNN` reference, AC number, ISS-NN reference, and reviewer name from logs and inline comments. Keep only comments that explain a non-obvious *why* and stand alone without project context.

---

## Cross-cutting observations

- The phase did NOT add a test for `stream_get` (GET reconnect) at all. Given that the POST path got both unit and integration coverage for the dedup invariant, the asymmetric coverage gap is what masks CR-01.
- `runner/channel.py` reconnect tests only cover the `RemoteProtocolError` (exception) recovery path, not the `>= 400` status path. CR-02 is invisible to the current suite.
- The integration test `test_stream_reconnect.py` correctly asserts `len(execution.events) == 3` rather than reading the SSE wire body (which the test acknowledges is lossy due to encoder-strict validation). This is a sound design choice but it means a future regression where the persister double-subscribes *but* the bus deduplicates would still pass. Stronger assertion: also check `spawn_count["n"] == 1` AND that `app.state.persist_tasks` size is 1 during the run.

---

_Reviewed: 2026-05-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
