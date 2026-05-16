---
phase: 01-backend-ag-ui-runner-correctness
plan: 03
subsystem: runner/audit
tags:
  - runner
  - audit
  - graceful-shutdown
  - iss-03
requirements:
  - ISS-03
dependency_graph:
  requires: []
  provides:
    - drain-then-cancel-stop
    - audit-completeness-on-graceful-shutdown
  affects:
    - runner/hooks/audit.py
    - tests/unit/test_audit_hook_drain.py
tech_stack:
  added: []
  patterns:
    - drain-then-cancel queue shutdown
    - bounded per-payload drain with per-request httpx timeout
key_files:
  created:
    - tests/unit/test_audit_hook_drain.py
  modified:
    - runner/hooks/audit.py
decisions:
  - "Keep the drain inline in stop() rather than exposing a separate flush() method (ISS-03 recommended option)"
  - "Tolerate transport errors during the drain (log and continue) so a single broken POST does not strand the remaining N-1 audit rows"
  - "runner/__main__.py unchanged: existing shutdown ordering already awaits audit_sender.stop() before channel.stop()"
metrics:
  duration: "~9 minutes"
  completed: "2026-05-16T00:34:58Z"
  tasks_completed: 2
  files_created: 1
  files_modified: 1
  tests_added: 4
  commits: 2
---

# Phase 01 Plan 03: Runner Audit Hook Drain (ISS-03) Summary

Drain `_after_queue` inside `AuditHookSender.stop()` before cancelling the worker so every queued `AfterToolCall` payload is POSTed on graceful shutdown — closing the audit-completeness gap that lost up to 256 `tool_call.completed` rows per warm-idle eviction.

## What Changed

### `runner/hooks/audit.py` — `AuditHookSender.stop()`

Replaced the prior cancel-then-aclose body with a three-step shutdown:

1. **Drain** `_after_queue` via a `get_nowait` / `_post(..., "AfterToolCall")` loop bounded by `asyncio.QueueEmpty`. Per-payload exceptions are logged (`audit.after_post_failed_during_drain`) and the loop continues — a single failed POST cannot strand the remaining payloads.
2. **Cancel** `_after_worker` and `await` it (existing `(asyncio.CancelledError, BaseException)` swallow preserved for idempotency).
3. **Aclose** `_http` and null it out (existing behaviour).

The drain runs only when both `_http` and `_after_worker` are non-None, so a second `stop()` call is a no-op on all three legs (queue already empty, task already done, http already None).

### `runner/__main__.py` — no change

The plan's interface review confirmed the shutdown ordering already awaits `audit_sender.stop()` (line 144) before `channel.stop()` (line 145) inside the `finally`. No edit required.

### `tests/unit/test_audit_hook_drain.py` — new file (4 tests)

| Test | Asserts |
| ---- | ------- |
| `test_stop_drains_after_queue_before_cancel` | 3 enqueued payloads → `_post` invoked 3× in FIFO order, all with `event_name == "AfterToolCall"`; queue empty; worker done; `_http` aclosed and nulled |
| `test_stop_continues_drain_on_post_failure` | 3 enqueued payloads, second `_post` raises `httpx.TimeoutException` → `stop()` does not re-raise; all 3 payloads still attempted |
| `test_stop_with_empty_queue_does_not_call_post` | Empty queue → 0 `_post` calls; `_http` still aclosed |
| `test_stop_is_idempotent` | `await stop(); await stop()` does not raise |

Tests construct the sender directly (no `start()`), attach a `_FakeHttp` stub with `aclose()`, and use `monkeypatch.setattr(AuditHookSender, "_post", ...)` so no socket is opened.

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ------ | ----- |
| RED  | Regression test (Task 2) | `7ab385ee5` | `tests/unit/test_audit_hook_drain.py` |
| GREEN | Drain-then-cancel fix (Task 1) | `e868ad2d1` | `runner/hooks/audit.py` |

Task 2 was committed first (test-first) so the RED→GREEN gate sequence is visible in git history; Task 1's acceptance criteria explicitly require the new test to pass, which it does post-fix.

## Verification

| Check | Result |
| ----- | ------ |
| `.venv/bin/python -m pytest tests/unit/test_audit_hook_drain.py -x -q` | 4 passed in 0.10s |
| `.venv/bin/python -m pytest tests/unit/ -q` | 94 passed, 1 skipped (unrelated Postgres skip in `test_repos.py`); 0 failures |
| `get_nowait` present in `stop()` body | 1 occurrence |
| `asyncio.QueueEmpty` present in `stop()` body | 1 occurrence |
| `_post(..., "AfterToolCall")` present in `stop()` body | 1 occurrence |
| Source order: `get_nowait` line precedes `_after_worker.cancel()` line | drain at body-line 11; cancel at body-line 19 |
| `runner/__main__.py:144` still `await audit_sender.stop()` | preserved |
| No socket / httpx client opened in the new tests | confirmed (sender constructed without `start()`, `_http` replaced with stub) |

## Deviations from Plan

None — plan executed exactly as written.

The plan listed the implementation task (Task 1) before the regression test (Task 2). For a clean TDD gate sequence in git history I committed the test first (RED — fails) and the fix second (GREEN — passes). The deliverables of both tasks are unchanged; only the commit ordering differs from the textual order in the plan.

## Pre-existing Conditions

- The repository ships a fresh `.venv` per worktree; `uv sync --frozen --extra dev` is required before the first test run to install pytest into the venv. Subsequent runs use `.venv/bin/python -m pytest …`. No changes to `pyproject.toml` were needed.

## Risks / Follow-ups

- **Worst-case drain time on a fully-saturated queue:** 256 × 2 s = 512 s. ISS-03 explicitly accepts this bound; if eviction latency becomes an operational issue, a future change could either (a) cap the drain by wall-clock budget and emit a `HookBackpressure` for the residual, or (b) parallelise drain POSTs. Out of scope here.
- **Single-runner audit completeness only:** This fix closes the per-runner shutdown path. Other audit-loss surfaces (network partition during `_after_loop`, backend 5xx without retry) remain handled by `_post`'s `raise_for_status` + the `_after_loop` log-and-continue; no change.

## Self-Check: PASSED

- `tests/unit/test_audit_hook_drain.py` exists: confirmed (`-f` check)
- Commit `7ab385ee5` exists in `git log --all`: confirmed
- Commit `e868ad2d1` exists in `git log --all`: confirmed
- `runner/hooks/audit.py` contains the drain loop in `stop()`: confirmed (grep + awk inspection above)
- `runner/__main__.py` unmodified vs. baseline: confirmed (`git diff` empty)
- Full unit suite green: 94 passed
