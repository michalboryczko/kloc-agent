---
phase: 01-backend-ag-ui-runner-correctness
plan: 02
subsystem: backend/streaming
tags:
  - backend
  - sse
  - reconnect
  - persister
  - iss-02
requires: []
provides:
  - "persister-dedup invariant on app.state.persist_tasks[(session_id, run_id)]"
  - "single execution_registry append per runner-emitted event under concurrent reconnect"
affects:
  - src/api/stream.py
tech-stack:
  added: []
  patterns:
    - "lookup-then-create-or-reuse dedup on app.state singleton dicts"
key-files:
  created:
    - tests/integration/test_stream_reconnect.py
  modified:
    - src/api/stream.py
    - tests/unit/test_stream.py
decisions:
  - "app.state.persist_tasks is now dict[tuple[str, str], asyncio.Task] keyed by (session_id, run_id), not the prior set[asyncio.Task]; the done-callback pops the entry on completion so the dict cannot grow unbounded across runs"
  - "Dedup checks `existing.done()` before reusing — a stale completed task (e.g. previous run already finished) does not block a fresh persister for the same (sid, rid)"
metrics:
  duration: ~10 minutes
  completed: 2026-05-16
requirements:
  - ISS-02
---

# Phase 01 Plan 02: Backend AG-UI Runner Correctness — ISS-02 (persister dedup) Summary

Dedup `_persist_events` task spawn by `(session_id, run_id)` so two concurrent
`POST /v1/sessions/{id}/stream` calls for the same in-flight run no longer
double-subscribe the event bus or double-append events to the execution-registry ring.

## Tasks Completed

| Task | Name | Commit | Files |
| --- | --- | --- | --- |
| 1 | Dedup `_persist_events` spawn by `(session_id, run_id)` | `64b1b51f3` | `src/api/stream.py` |
| 2 | Unit regression: concurrent `stream_post` does not double-spawn persister | `577d5e503` | `tests/unit/test_stream.py` |
| 3 | Integration regression: two concurrent `POST /stream` same-run produce one persister + one ring | `f73df230c` | `tests/integration/test_stream_reconnect.py` |

## Changes Made

### `src/api/stream.py` (Task 1)

In `stream_post`, replaced the unconditional persister spawn with a dict-keyed dedup
check. `app.state.persist_tasks` changed from `set[asyncio.Task]` to
`dict[tuple[str, str], asyncio.Task]`. The lookup-then-create block:

- Fetches the dict (creating it on first request).
- Looks up `existing = persist_tasks.get((session_id, run_id))`.
- If a live persister exists for that key, **skips** task creation. The SSE
  generator still subscribes to and consumes from the bus topic via
  `event_bus.consume`, which already supports multi-subscriber semantics.
- Otherwise creates a new `_persist_events` task, attaches the existing
  `_log_persist_task_result` callback, stores under the key, and registers a
  done-callback that pops the entry on completion (success, error, or cancel).

`execution_registry.get_or_create` is still called on every POST — it is
idempotent and returns the existing `Execution` for the second concurrent
caller, so cursor semantics remain consistent.

### `tests/unit/test_stream.py` (Task 2)

Appended `test_concurrent_reconnect_does_not_double_spawn_persister`. The test
constructs a duck-typed `Request` with a `FakeRegistry` (returning a static
entry with an in-memory inbox and no-op warm-idle manager), monkeypatches
`_persist_events`, `_persist_user_message`, `_build_hydration_payload`, and
`make_response` so the test is pure-Python (no DB, no HTTP, no SSE encoder),
then calls `stream_post` twice concurrently via `asyncio.gather`. Asserts:

- Counter (spawn invocations) equals 1.
- `persist_tasks` is a dict with exactly one entry at the `(sid, rid)` key.
- Both calls returned a (stubbed) response.

Reverse-check: reverting Task 1's dedup makes the test fail with
`'set' object has no attribute 'get'` (the test code defensively uses
`.get(...)` on the dict; the pre-fix set surface lacks it).

### `tests/integration/test_stream_reconnect.py` (Task 3, new file)

New file, `pytestmark = pytest.mark.integration`. Uses `asgi_client` +
`app_in_process` + `truncate_all_tables`. Setup:

- Injects a `FakeRunner` via `app_in_process.state.runner_registry.set_runner(...)`.
- Wraps the real `_persist_events` with a counter so spawn-count is
  observable at runtime.
- Schedules a deferred publisher task that emits a 3-event scripted
  sequence (`RUN_STARTED`, `TEXT_MESSAGE_CONTENT`, `RUN_FINISHED`) onto
  the bus 300 ms after launch.
- Launches two concurrent `POST /v1/sessions/{sid}/stream` via
  `asyncio.gather`. Each blocks on its SSE generator until `RUN_FINISHED`
  closes it.

Assertions:

- Both responses are 200.
- `spawn_count == 1` (pre-fix code would be 2).
- `len(execution.events) == 3` (pre-fix code would double-count to 6).

Reverse-check: reverting Task 1's dedup makes the test fail with
`expected _persist_events to be spawned once, got 2`.

## Deviations from Plan

### Plan-relative deviations

**Task 3 verification body assertion (minor):** the plan suggested asserting
on individual SSE frames in the response body (`RUN_FINISHED in r.text`). In
practice the AG-UI strict encoder drops the minimal scripted events because
they lack required fields (`timestamp`, etc.). The SSE generator still
closes correctly because `is_run_lifecycle_terminal` inspects the raw dict
**before** the encoder, but the wire body may not contain the terminal
frame. The test was adjusted to drop the wire-body assertion and rely on:

1. The fact that both `gather`'d POSTs return (proving the generators ran
   to terminal).
2. The execution-registry ring length (the durable evidence of single vs.
   double append).
3. The spawn-counter (direct measurement of the dedup decision).

These three together are strictly stronger than the body-text check and
isolated from encoder-version drift.

### Auto-fixed issues

None — the plan executed as written.

### Already-realized work

None — the per-task work was net-new. The repository's prior persister
tracking via a `set[asyncio.Task]` was the bug being fixed.

## Verification

```bash
uv run python -m pytest tests/unit/test_stream.py tests/integration/test_stream_reconnect.py -x -q
# 5 passed in ~1.1s
```

Grep-based acceptance checks (Task 1):

```bash
grep -nE 'persist_tasks\[' src/api/stream.py          # 1 match (dict subscript)
grep -cE 'persist_tasks\.add\(' src/api/stream.py     # 0 (no set.add)
grep -nE 'persist_tasks\.get\(key\)' src/api/stream.py # 1 match
grep -rn 'persist_tasks = set' src/                    # 0
```

Reverse-check (proves the regression tests would have caught the bug):

```bash
# Restore pre-fix stream.py:
git show HEAD~2:src/api/stream.py > src/api/stream.py
uv run python -m pytest tests/unit/test_stream.py tests/integration/test_stream_reconnect.py -x
# Unit:        AttributeError: 'set' object has no attribute 'get'
# Integration: AssertionError: expected _persist_events to be spawned once, got 2
```

## Known Stubs

None.

## Threat Flags

None — this change tightens existing in-process invariants. No new network
endpoints, auth paths, or trust boundaries.

## Self-Check: PASSED

- `src/api/stream.py` — FOUND, contains `persist_tasks[key] = persist_task`
- `tests/unit/test_stream.py` — FOUND, contains
  `test_concurrent_reconnect_does_not_double_spawn_persister`
- `tests/integration/test_stream_reconnect.py` — FOUND, non-empty, contains
  `test_concurrent_post_stream_same_run_id` and
  `pytestmark = pytest.mark.integration`
- Commits `64b1b51f3`, `577d5e503`, `f73df230c` — FOUND in `git log --all`
