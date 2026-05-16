---
phase: 01-backend-ag-ui-runner-correctness
plan: 01
subsystem: backend
tags:
  - backend
  - ag-ui
  - event-ordering
  - run-lifecycle
  - bugfix
  - tdd
dependency-graph:
  requires: []
  provides:
    - "AG-UI lifecycle ordering invariant: RUN_STARTED published at index 0 of each (session, run) topic"
    - "Race-safe active_run_by_session mapping under concurrent run handover"
  affects:
    - src/api/internal.py
    - tests/unit/test_internal.py
tech-stack:
  added: []
  patterns:
    - "Pure-dict compare-and-swap in single-uvicorn-worker process state"
    - "Publish-then-flush ordering for AG-UI lifecycle correctness"
key-files:
  created: []
  modified:
    - path: src/api/internal.py
      what: "_dispatch_frame: orphan-buffer flush moved after RUN_STARTED publish; CAS guard on active_run_by_session.pop"
    - path: tests/unit/test_internal.py
      what: "Five new regression tests covering ISS-01 ordering and ISS-04 stale-run handover (incl. RUN_ERROR parity)"
decisions:
  - "Added an extra parity test for RUN_ERROR alongside the four plan-named tests so the CAS guard's RUN_ERROR branch is also asserted (Rule 2: missing critical coverage)."
  - "Did not modify _diag instrumentation — explicitly out of scope per plan (belongs to ISS-08)."
requirements:
  - ISS-01
  - ISS-04
metrics:
  duration: "~18 min"
  completed_date: "2026-05-16"
  tasks_completed: 3
  files_modified: 2
  commits: 4
---

# Phase 01 Plan 01: Backend AG-UI & Runner Correctness — ISS-01 + ISS-04 Summary

ISS-01 (lifecycle ordering): subscribers now see `RUN_STARTED` at index 0 of the `(session, run)` bus topic before any previously-buffered pre-run orphan frames are flushed. ISS-04 (race-safe handover): a stale `RUN_FINISHED` for a prior run no longer wipes the active-run mapping of a fresh run that has already taken over the session — its subsequent intermediate frames route through `active_run_by_session` instead of being misrouted to the orphan buffer.

## Tasks Completed

| # | Task | Type | Commit | Verified |
|---|------|------|--------|----------|
| 1 | ISS-01 RED: add failing test for RUN_STARTED ordering | test | `e196a30bb` | failed pre-fix with correct diff (`[TEXT_MESSAGE_CONTENT, …, RUN_STARTED]` instead of `[RUN_STARTED, …]`) |
| 1 | ISS-01 GREEN: publish RUN_STARTED before flushing orphan buffer | fix | `36fd02e14` | `pytest tests/unit/test_internal.py` 5/5 pass |
| 2 | ISS-04 RED: add failing tests for stale `RUN_FINISHED`/`RUN_ERROR` + e2e orphan-routing scenario | test | `bfd2c826a` | three stale-run tests failed; baseline current-run test still passed |
| 2 | ISS-04 GREEN: CAS guard on `active_run_by_session.pop` | fix | `d2154228b` | `pytest tests/unit/test_internal.py` 9/9 pass; `pytest tests/unit/test_event_bus.py` 4/4 pass (no regression on adjacent module) |
| 3 | Verify all four plan-named regression tests present | n/a | (folded into Tasks 1 + 2 RED commits) | `grep` confirms all 4 plan-required tests + 1 parity test |

Each task followed RED → GREEN with a separate commit per gate. Task 3 of the plan (regression-test inventory) was satisfied by the RED commits of Tasks 1 and 2, so no separate commit was needed — the four plan-required tests live in `tests/unit/test_internal.py` and are verified below.

## Code Changes

### `src/api/internal.py:_dispatch_frame`

1. **ISS-01 ordering fix.** The orphan-buffer flush previously lived inside the `run_id`/`RUN_STARTED` setter block (lines 116–130 of the pre-fix file), so it ran before the bottom-of-function `bus.publish` line that emits the current frame. Moved the flush to a new block immediately after the current-frame publish, gated on `frame_type == "RUN_STARTED"`. Source order is now:

   ```
   await bus.publish(session_id, str(run_id), frame)         # line 150 — RUN_STARTED itself
   if frame_type == "RUN_STARTED" and pending_by_session …:  # line 160
       for buf in pending: await bus.publish(…, buf)          # line 164 — buffered orphans
   ```

2. **ISS-04 CAS guard.** Replaced the unconditional `active_by_session.pop(session_id, None)` in the terminal-frame cleanup block with `if active_by_session.get(session_id) == str(run_id): active_by_session.pop(session_id, None)`. A late `RUN_FINISHED(rA)` arriving after a fresh `RUN_STARTED(rB)` overwrote the mapping no longer wipes B's routing key — confirmed by `test_stale_run_finished_does_not_orphan_subsequent_b_frame` which dispatches a stale `RUN_FINISHED(rA)` then a no-`runId` intermediate frame and asserts it was published under `rB`, not orphan-buffered.

### `tests/unit/test_internal.py`

Five new `async def test_…` functions appended (using the existing `_FakeBus` / `_make_request` helpers; no new fixture file):

1. `test_run_started_publishes_before_buffered_orphans` — plan-required; asserts `[RUN_STARTED, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_CONTENT]` order on the bus.
2. `test_run_finished_for_current_run_clears_active_mapping` — plan-required; baseline so the CAS guard doesn't over-correct end-of-run cleanup.
3. `test_run_finished_for_stale_run_does_not_wipe_active_mapping` — plan-required; the core ISS-04 assertion.
4. `test_run_error_for_stale_run_does_not_wipe_active_mapping` — added beyond plan to give `RUN_ERROR` parity coverage; see Deviations.
5. `test_stale_run_finished_does_not_orphan_subsequent_b_frame` — plan-required; the e2e routing scenario.

## Verification

```
$ uv run --extra dev pytest tests/unit/test_internal.py tests/unit/test_event_bus.py -v
13 passed in 0.33s
```

Plan-mandated acceptance checks (all satisfied):

| AC | Command | Result |
|----|---------|--------|
| Publish ordering in source | `grep -n 'await bus.publish' src/api/internal.py` | line 150 (RUN_STARTED frame) precedes line 164 (buffered flush) |
| CAS guard present exactly once | `grep -nE 'active_by_session\.get\(session_id\) == str\(run_id\)' src/api/internal.py` | 1 match (line 181) |
| Non-comment pop occurrences | `grep -cE '^[^#]*active_by_session\.pop\(session_id' src/api/internal.py` | 1 |
| Four named regression tests exist | `grep -nE '(test_run_started_publishes_before_buffered_orphans\|test_run_finished_for_stale_run_does_not_wipe_active_mapping\|test_run_finished_for_current_run_clears_active_mapping\|test_stale_run_finished_does_not_orphan_subsequent_b_frame)' tests/unit/test_internal.py \| wc -l` | 4 |
| ISS-01 ordering test passes | `pytest tests/unit/test_internal.py::test_run_started_publishes_before_buffered_orphans` | passed |
| ISS-04 stale-run test passes | `pytest tests/unit/test_internal.py::test_stale_run_finished_does_not_orphan_subsequent_b_frame` | passed |
| No regression on adjacent module | `pytest tests/unit/test_event_bus.py` | 4 passed |

RED-phase evidence (confirming the new tests would have caught the original bugs):

- ISS-01 RED diff: `[TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_CONTENT, RUN_STARTED]` actual vs `[RUN_STARTED, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_CONTENT]` expected — exactly the ordering inversion described in the plan.
- ISS-04 RED diag output for the orphan-routing scenario, captured via stderr in the failing run: `EVENTS BUFFERED (no active run): type=TEXT_MESSAGE_CONTENT session_id=s1 buffered=1` — confirms B's intermediate frame was misrouted to the orphan buffer because A's stale `RUN_FINISHED` wiped the mapping.

## Deviations from Plan

### Beyond-plan additions

**1. [Rule 2 — Missing critical coverage] Added `test_run_error_for_stale_run_does_not_wipe_active_mapping`**

- **Found during:** Task 2 RED authoring.
- **Issue:** The CAS guard applies to both `RUN_FINISHED` and `RUN_ERROR` (same `if` branch), and the plan's behaviour spec states "The same holds for `RUN_ERROR` frames", but only `RUN_FINISHED` cases are explicitly listed in the four plan-named tests. Without a `RUN_ERROR` assertion, a future regression that special-cases `RUN_FINISHED` only would slip past CI.
- **Fix:** Added one extra test that mirrors the stale-`RUN_FINISHED` assertion using `RUN_ERROR`.
- **Files modified:** `tests/unit/test_internal.py`
- **Commit:** `bfd2c826a` (same RED commit as the plan-required tests).

### Auto-fixed issues

None. The plan was executable as written.

### Explicitly NOT done (per plan scope)

- Did not remove or alter any `_diag(...)` instrumentation — out of scope (belongs to ISS-08).
- Did not change `_PRE_RUN_BUFFER_CAP`, the heartbeat branch, the `on_run_finished` notification, or the orphan-buffering branch for frames without a run id.

## Commits

| Hash | Type | Message (short) |
|------|------|-----------------|
| `e196a30bb` | test | add failing regression test for ISS-01 ordering |
| `36fd02e14` | fix | publish RUN_STARTED before flushing pre-run orphan buffer |
| `bfd2c826a` | test | add failing regression tests for ISS-04 CAS guard |
| `d2154228b` | fix | CAS guard on active_run_by_session.pop for terminal frames |

## Known Stubs

None.

## Threat Flags

None — this plan modifies in-process event routing only; no new network endpoints, auth paths, file access, or trust boundaries.

## Self-Check

FOUND: src/api/internal.py
FOUND: tests/unit/test_internal.py
FOUND: e196a30bb
FOUND: 36fd02e14
FOUND: bfd2c826a
FOUND: d2154228b
FOUND: test_run_started_publishes_before_buffered_orphans (tests/unit/test_internal.py:91)
FOUND: test_run_finished_for_current_run_clears_active_mapping (tests/unit/test_internal.py:124)
FOUND: test_run_finished_for_stale_run_does_not_wipe_active_mapping (tests/unit/test_internal.py:138)
FOUND: test_stale_run_finished_does_not_orphan_subsequent_b_frame (tests/unit/test_internal.py:165)

## Self-Check: PASSED
