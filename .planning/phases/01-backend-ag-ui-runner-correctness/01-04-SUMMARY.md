---
phase: 01-backend-ag-ui-runner-correctness
plan: 04
subsystem: runner
tags: [runner, reconnect, transport, event-loss, ag-ui, httpx, jsonl]

requires:
  - phase: 01-backend-ag-ui-runner-correctness
    provides: outbound stream reconnect loop with pending_after_break queue drain
provides:
  - last_inflight tracking in runner channel — the most recently yielded frame survives a mid-emit transport reset
  - regression suite asserting in-flight RUN_FINISHED replay, no-duplicate clean shutdown, and ordered in-flight + drained-queue replay
affects: [01-05, runner-stream-correctness, RUN_FINISHED-delivery]

tech-stack:
  added: []
  patterns:
    - "Track the most recently yielded frame across stream reconnect attempts; over-deliver on reconnect to prevent silent loss"
    - "Unit-test async httpx-style context managers with a fake CM that drives the body iterator manually — no real network"

key-files:
  created: []
  modified:
    - runner/channel.py
    - tests/unit/test_channel.py

key-decisions:
  - "Prepend last_inflight to pending_after_break on both the except-branch and 4xx/5xx continue paths (body not consumed by backend in either case)"
  - "Update last_inflight inside body_iter's replay loop too, so a failure during replay still re-replays the same frame"
  - "Skip ack-based watermarking — over-delivery is cheaper and matches existing backend semantics for AG-UI intermediates"

patterns-established:
  - "last_inflight tracking: capture the most recently handed-to-transport frame, re-send at the head of the next stream attempt"
  - "Fake httpx stream CM for unit tests: __aenter__ drives the body iter manually, raises mid-block to simulate transport reset"

requirements-completed: [ISS-06]

duration: ~30min
completed: 2026-05-16
---

# Phase 01 Plan 04: Runner Channel In-Flight Frame Replay Summary

**Track last_inflight across reconnect attempts so a mid-emit transport reset no longer silently drops the yielded-but-unflushed frame (closes "missing RUN_FINISHED" symptom).**

## Performance

- **Duration:** ~30 min
- **Started:** 2026-05-16T00:08Z
- **Completed:** 2026-05-16T00:39Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- `_stream_outbound` now tracks `last_inflight: dict | None` across reconnect attempts in `runner/channel.py`. `body_iter` updates it immediately before every yield (in both the `pending_after_break` replay loop and the main `_outbound` pull loop).
- On the transport-exception path, `last_inflight` is prepended to `pending_after_break` BEFORE the queue drain, then cleared — so the next stream attempt's body iter replays the in-flight frame at the head, followed by the drained queue contents, preserving order.
- The same prepend-and-clear is applied on the 4xx/5xx `continue` path (backend did not consume the body).
- Three regression tests added in `tests/unit/test_channel.py`:
  - `test_reconnect_replays_last_inflight_frame` — simulates mid-emit reset; asserts `TEXT_MESSAGE_CONTENT`, `TEXT_MESSAGE_END`, `RUN_FINISHED` all arrive on attempt 2 in order.
  - `test_clean_shutdown_does_not_resend_last_frame` — None-sentinel close emits each event exactly once.
  - `test_reconnect_preserves_order_when_inflight_and_queue_both_present` — in-flight (E1) precedes drained queue (E2, E3).
- All three tests fail against pre-fix code (verified manually by temporarily reverting `runner/channel.py` to HEAD~1) and pass against the fixed code.

## Task Commits

1. **Task 1: Track last_inflight in body_iter and prepend on reconnect** — `f929af8fd` (fix)
2. **Task 2: Regression test — mid-emit transport exception preserves the in-flight frame** — `a4acea415` (test)

## Files Created/Modified

- `runner/channel.py` — added `last_inflight` declaration, `nonlocal` assignment inside `body_iter` for both replay and main loops, prepend-and-clear in the `except Exception` branch (before the queue-drain loop) and in the 4xx/5xx continue path.
- `tests/unit/test_channel.py` — added `_FakeResponse`, `_FakeStreamCM`, `_FakeHttp` helpers plus three regression test functions. Tests use no real network or real `httpx.AsyncClient`; they drive `_stream_outbound` directly via `await chan._stream_outbound()` and inspect collected bytes on each simulated attempt.

## Decisions Made

- Prepended `last_inflight` on BOTH the transport-exception path and the 4xx/5xx response path. Rationale: in both cases the backend did not durably consume the in-flight frame, so over-delivery is the safe default; the alternative (acknowledgement-based watermarking) was rejected in ISS-06 as more invasive than the symptom requires.
- Reset `last_inflight = None` immediately after the prepend so a second consecutive failure during replay does not double-prepend the same frame.
- Patched `runner.channel.asyncio.sleep` in tests to skip exponential backoff (each test bounded by `asyncio.wait_for(..., timeout=5.0)`). Captured the real `asyncio.sleep` reference BEFORE patching to avoid recursion through the global `asyncio` module attribute.

## Deviations from Plan

None — plan executed as written.

The plan's Task 2 acceptance criteria specified that the regression tests should fail against pre-fix code. Verified manually by temporarily restoring `HEAD~1:runner/channel.py` and re-running the suite: `test_reconnect_replays_last_inflight_frame` fails (the second attempt's `_FakeStreamCM.collected` is empty because the except handler drains the sentinel queued for attempt 2 — the `last_inflight` prepend is what gives attempt 2's body iter anything to yield). The other two new tests also fail in the same way. Fix restored before commit.

## Issues Encountered

- **Test infrastructure**: `uv run pytest …` re-creates a venv that lacks the optional `dev` extras (pytest, pytest-asyncio). Resolved with `uv sync --extra dev` plus `uv run python -m pytest …` so the existing venv is used. This is a pre-existing baseline condition, not introduced by this plan; documented here for the next executor.
- **`asyncio.sleep` patching recursion**: First draft of the in-test backoff patch referenced `asyncio.sleep` from the module namespace after patching it — the patched function recursed into itself. Fixed by capturing the real sleep into a local before mutating the module attribute.

## User Setup Required

None — change is fully internal to the runner channel.

## Next Phase Readiness

- Runner channel correctness improved: mid-stream backend resets no longer lose terminal AG-UI frames.
- Existing channel tests (`test_drain_outbound_queue_into_pending_buffer`, `test_emit_does_not_block_on_idle_queue`) still pass; the new pattern is purely additive on the reconnect path.
- The fake httpx stream CM in `tests/unit/test_channel.py` is a reusable test pattern for any future test of `_stream_outbound` that needs to simulate transport behaviour without a real backend.

## Self-Check: PASSED

- FOUND: runner/channel.py — last_inflight at line 151, body_iter assignments at 157/166, except-branch prepend at 224-226, 4xx/5xx prepend at 205-207
- FOUND: tests/unit/test_channel.py — three new test functions at lines 168, 222, 248
- FOUND: commit f929af8fd (fix Task 1)
- FOUND: commit a4acea415 (test Task 2)
- VERIFIED: `uv run python -m pytest tests/unit/test_channel.py -x -q` → 5 passed
- VERIFIED: regression tests fail against `HEAD~1:runner/channel.py` (manual reverse-check)

---
*Phase: 01-backend-ag-ui-runner-correctness*
*Completed: 2026-05-16*
