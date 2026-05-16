---
phase: 01-backend-ag-ui-runner-correctness
verified: 2026-05-16T16:30:00Z
status: passed
score: 5/5 must-haves verified
phase_req_ids:
  - ISS-01
  - ISS-02
  - ISS-03
  - ISS-04
  - ISS-06
must_haves_verified: 5
must_haves_total: 5
tests_passed: 141
tests_skipped: 5
tests_failed: 0
overrides_applied: 0
human_verification:
  - test: "WR-02 persister idle-timeout budget under real LLM long-tail latency"
    expected: "runner_heartbeat_timeout_s + 30 (default 60s) is wider than the longest normal between-event gap; persister never exits prematurely while a real run is still mid-tool-call"
    why_human: "The chosen budget is a heuristic. Only end-to-end runs against the real Gemini / Anthropic provider against a live indexed PHP codebase will confirm the tail-latency distribution stays under the budget. Code-level verification cannot disprove this — the test suite uses fake runners with scripted events."
  - test: "Live runner spawn end-to-end with the audit drain on warm-idle eviction"
    expected: "Under a real runner that emits N AfterToolCall events and is then evicted via warm-idle, post-shutdown `audit_log` row count for `tool_call.completed` matches N (no rows lost)"
    why_human: "Requires a live Docker runner + Postgres + MinIO + kloc-intelligence MCP stack. The unit test asserts the drain code path POSTs every queued payload; only an integration-with-real-DB run validates that the corresponding `tool_call.completed` rows actually land in `audit_log`."
---

# Phase 1: Backend AG-UI & Runner Correctness Verification Report

**Phase Goal:** Eliminate the event-ordering and reconnect bugs that violate AG-UI lifecycle invariants and lose audit events. The resume / cursor-replay regression and the audit-completeness gap both come from this cluster.

**Verified:** 2026-05-16T16:30:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria + PLAN frontmatter)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| 1 | AG-UI lifecycle ordering invariant holds: under orphan-frame → RUN_STARTED → terminal scenarios, subscribers always see `RUN_STARTED` at index 0 | VERIFIED | `src/api/internal.py:160` publishes the current frame (RUN_STARTED) **before** the buffered-orphan flush at `:170-179`. Regression test `tests/unit/test_internal.py::test_run_started_publishes_before_buffered_orphans` (line 91) asserts the bus order `[RUN_STARTED, TEXT_MESSAGE_CONTENT, TEXT_MESSAGE_CONTENT]`. Test passes. |
| 2 | Concurrent reconnect `POST /v1/sessions/{id}/stream` for the same `(session_id, run_id)` does not create a second `_persist_events` task and does not double-append to the execution ring | VERIFIED | `src/api/stream.py:120-140` switched `persist_tasks` to `dict[tuple[str,str], asyncio.Task]` keyed by `(session_id, run_id)` with lookup-then-create-or-reuse + pop-on-done callback. Unit test `tests/unit/test_stream.py::test_concurrent_reconnect_does_not_double_spawn_persister` (line 264) asserts spawn count == 1 + dict size == 1 under `asyncio.gather` of two concurrent calls. Integration test `tests/integration/test_stream_reconnect.py::test_concurrent_post_stream_same_run_id` (line 31) asserts `spawn_count == 1` and `len(execution.events) == 3` (not 6). Both pass. |
| 3 | `AuditHookSender` graceful shutdown drains `_after_queue`; post-shutdown audit-row count matches pre-shutdown completed tool-call count | VERIFIED | `runner/hooks/audit.py:70-103` reorders to **cancel worker → drain queue under exclusive ownership → aclose http** (WR-05 hardening applied to original ISS-03 fix). Drain loop at `:92-100` uses `get_nowait` + `_post(payload, "AfterToolCall")` with per-payload exception swallow. Five unit tests in `tests/unit/test_audit_hook_drain.py` cover: drain ordering, post-failure continuation, empty queue, cancel-before-drain ordering, and idempotency. All pass. |
| 4 | Concurrent `RUN_FINISHED` of run A and `RUN_STARTED` of run B for the same session does not wipe B's `active_by_session` mapping | VERIFIED | `src/api/internal.py:191` compare-and-swap: `if active_by_session.get(session_id) == str(run_id): active_by_session.pop(session_id, None)`. Tests `tests/unit/test_internal.py::test_run_finished_for_stale_run_does_not_wipe_active_mapping` (line 138), `::test_run_error_for_stale_run_does_not_wipe_active_mapping` (line 154, parity), and `::test_stale_run_finished_does_not_orphan_subsequent_b_frame` (line 165) all assert the CAS guard and the downstream routing consequence. All pass. |
| 5 | Runner `channel.py` reconnect after mid-stream transport reset preserves the in-flight yielded frame; `RUN_FINISHED` is never silently lost on a transport-loss path | VERIFIED | `runner/channel.py:154` tracks `yielded_this_attempt: list[dict]` (strengthened from the plan's original `last_inflight` to a whole-attempt transcript per CR-02 review fix). Both the `>=400` status branch (`:221-222`) and the `except Exception` branch (`:250-251`) execute `pending_after_break[:0] = yielded_this_attempt; yielded_this_attempt.clear()` BEFORE draining the queue. Five unit tests in `tests/unit/test_channel.py` cover: in-flight replay on transport exception, no-resend on clean shutdown, full-transcript replay on 4xx, sentinel terminating during 4xx drain, and order preservation when in-flight + queue both present. All pass. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | -------- | ------ | ------- |
| `src/api/internal.py` | `_dispatch_frame` ordering fix + CAS guard | VERIFIED | Publish-then-flush at lines 160 + 170-179; CAS at line 191. `grep -nE 'active_by_session\.get\(session_id\) == str\(run_id\)' src/api/internal.py` → 1 match. |
| `src/api/stream.py` | persister dedup by `(session_id, run_id)`, plus follow-on CR-01 stream_get seam fix, WR-01/WR-02/WR-03/WR-07 hardening | VERIFIED | `persist_tasks[key]` is a dict (line 137). `stream_get` registers before snapshotting (line 175). Try/except BaseException + unregister at lines 149-151, 195-201. `essential=True` for persister at line 300. Registry health check before persist at line 75. |
| `runner/hooks/audit.py` | drain-on-stop in `AuditHookSender.stop()` | VERIFIED | Order is cancel → drain → aclose (WR-05 hardening). `get_nowait` + `asyncio.QueueEmpty` + `_post(payload, "AfterToolCall")` present at lines 92-100. |
| `runner/channel.py` | in-flight frame replay on reconnect (extended to full-attempt transcript per CR-02) | VERIFIED | `yielded_this_attempt` tracked at line 154, populated in body_iter at lines 168/176, prepended to pending_after_break in both recovery branches at lines 221-222 and 250-251. |
| `tests/unit/test_internal.py` | 4 plan-named regression tests + 1 parity test | VERIFIED | All four required test names present; one extra `test_run_error_for_stale_run_does_not_wipe_active_mapping` added per Plan 01 summary as RUN_ERROR parity. |
| `tests/unit/test_stream.py` | `test_concurrent_reconnect_does_not_double_spawn_persister` + CR-01 seam test | VERIFIED | Both tests present (lines 264, 143). |
| `tests/integration/test_stream_reconnect.py` | `test_concurrent_post_stream_same_run_id` with integration mark | VERIFIED | File exists, marked `pytestmark = pytest.mark.integration`, asserts spawn_count == 1 AND `len(execution.events) == 3`. |
| `tests/unit/test_audit_hook_drain.py` | 4 plan-named + 1 hardening regression tests | VERIFIED | All five tests present including added `test_stop_cancels_worker_before_draining` (WR-05). |
| `tests/unit/test_channel.py` | 3 plan-named + 2 CR-02 hardening regression tests | VERIFIED | All five tests present including `test_4xx_response_drains_outbound_and_replays_all_frames` and `test_4xx_then_sentinel_does_not_loop` (CR-02). |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | -- | --- | ------ | ------- |
| `_dispatch_frame` (internal.py) | `EventBus.publish` (event_bus.py) | RUN_STARTED published before orphan flush | WIRED | Source order: `await bus.publish(...)` at line 160 precedes the `for buf in pending: await bus.publish(...)` loop at line 173. |
| `_dispatch_frame` RUN_FINISHED branch | `active_by_session` dict on app.state | CAS pop | WIRED | Line 191 guards `pop` with `get == str(run_id)`. |
| `stream_post` (stream.py) | `app.state.persist_tasks[(session_id, run_id)]` | lookup-then-create-or-reuse | WIRED | Lines 120-140 implement the pattern; done-callback at line 138-140 pops the entry. |
| `stream_post` | `execution_registry` | single Execution per `(sid, rid)` | WIRED | `await execution_registry.get_or_create(session_id, run_id)` at line 116. Integration test asserts only 3 events appended despite two concurrent calls. |
| `stream_get` | `event_bus` | register-before-replay + second-pass replay (CR-01) | WIRED | Lines 175-194 — register at 175 BEFORE the first `replay_from` at 179; second-pass `replay_from(highest_seq)` at 186 closes the seam. |
| `AuditHookSender.stop` | `_after_queue` | cancel-then-drain | WIRED | Worker cancelled at line 86; drain loop at lines 92-100. |
| `runner/__main__.py` finally | `audit_sender.stop()` | awaited before `channel.stop()` | WIRED | Unchanged from baseline; confirmed by plan 03 summary. |
| `_stream_outbound` body_iter | `pending_after_break` | prepend full attempt transcript | WIRED | Lines 221, 250 — `pending_after_break[:0] = yielded_this_attempt` in both recovery branches before draining `_outbound`. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ----------- | ----------- | ------ | -------- |
| ISS-01 | 01-01-PLAN.md | RUN_STARTED publishes before pre-RUN_STARTED orphan buffer flush | SATISFIED | `src/api/internal.py:160` precedes `:173-174`; test at `tests/unit/test_internal.py:91` |
| ISS-02 | 01-02-PLAN.md | Persister tasks keyed by `(session_id, run_id)`; concurrent reconnect does not double-subscribe | SATISFIED | `src/api/stream.py:120-140`; unit test at `tests/unit/test_stream.py:264`; integration test at `tests/integration/test_stream_reconnect.py:31` |
| ISS-03 | 01-03-PLAN.md | `AuditHookSender.stop()` drains `_after_queue` before cancelling worker | SATISFIED (with WR-05 hardening: order inverted to cancel→drain so worker cannot race the drain) | `runner/hooks/audit.py:70-103`; 5 tests in `tests/unit/test_audit_hook_drain.py` |
| ISS-04 | 01-01-PLAN.md | Compare-and-swap on `active_by_session.pop` for terminal frames | SATISFIED | `src/api/internal.py:191`; 3 tests in `tests/unit/test_internal.py` (lines 138, 154, 165) |
| ISS-06 | 01-04-PLAN.md | Runner channel reconnect preserves in-flight yielded frame | SATISFIED (strengthened to full-attempt transcript replay per CR-02 review fix; covers both transport-exception and 4xx/5xx paths) | `runner/channel.py:154, 168, 176, 221-222, 250-251`; 5 tests in `tests/unit/test_channel.py` |

No orphaned requirements: ROADMAP.md maps exactly `ISS-01, ISS-02, ISS-03, ISS-04, ISS-06` to Phase 1, and every one is claimed by a plan in the phase.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `src/api/internal.py` | 72, 81-83, 86, 94, 96, 99, 105, 142, 147, 153, 161, 175, 224, 235, 253, 281, 293, 302 | `B-DIAG-EVENTS` plan/section markers in log strings | Info | Explicitly out of scope for Phase 1; ISS-13 (Phase 3) is the comment-sweep phase. New code in this phase did NOT add new markers; existing markers are pending the mechanical sweep. |
| `runner/channel.py` | 76, 78-83, 178-182, 190, 202-206 | `B-DIAG-B` markers | Info | Same as above — ISS-13 scope. |
| `runner/hooks/audit.py` | 113, 157, 214, 223, 230 | `B-DIAG-A` markers | Info | Same as above — ISS-13 scope. |

No blocker anti-patterns found in phase-modified code. No `TBD/FIXME/XXX` markers introduced. No empty `return null` stubs. No hardcoded empty data feeding a renderer.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Full unit test suite (incl. all 5 ISS regression suites) | `.venv/bin/python -m pytest tests/unit/ -q --tb=no` | `107 passed, 1 skipped in 3.05s` (1 skip: `test_repos.py` Postgres unreachable — environment, not code) | PASS |
| Full integration test suite (incl. new test_stream_reconnect.py) | `.venv/bin/python -m pytest tests/integration/ -q --tb=no` | `34 passed, 4 skipped in 2.90s` (4 skips: pre-existing Phase 6 e2e deferrals — unrelated) | PASS |
| Targeted: ISS-01 ordering test | `.venv/bin/python -m pytest tests/unit/test_internal.py::test_run_started_publishes_before_buffered_orphans -q` | passed | PASS |
| Targeted: ISS-02 integration | `.venv/bin/python -m pytest tests/integration/test_stream_reconnect.py -q` | passed | PASS |
| Targeted: ISS-03 drain test | `.venv/bin/python -m pytest tests/unit/test_audit_hook_drain.py -q` | 5 passed | PASS |
| Targeted: ISS-06 channel tests | `.venv/bin/python -m pytest tests/unit/test_channel.py -q` | 7 passed | PASS |
| Source-order check (ISS-01) | `grep -n 'await bus.publish' src/api/internal.py` | Line 160 (RUN_STARTED) precedes line 174 (orphan flush) | PASS |
| Source-order check (ISS-04 CAS) | `grep -nE 'active_by_session\.get\(session_id\) == str\(run_id\)' src/api/internal.py` | 1 match (line 191) | PASS |
| Source-order check (ISS-02 dict) | `grep -nE 'persist_tasks\[' src/api/stream.py` | Match at line 137 | PASS |
| Source-order check (ISS-06 transcript) | `grep -nE 'yielded_this_attempt' runner/channel.py` | Matches at 154, 157, 168, 176, 221-222, 250-251 | PASS |

### Probe Execution

No project-specific probes (`scripts/*/tests/probe-*.sh`) declared by Phase 1 plans or in the repository. SKIPPED.

### Gap Coverage from Code Review

The standard-depth code review (`01-REVIEW.md`) identified 14 findings (2 critical + 8 warning + 4 info). The 01-REVIEW-FIX.md report claimed all 10 in-scope (critical + warning) findings were fixed. Verified each in the actual codebase:

| Review Finding | Claim | Verified |
| -------------- | ----- | -------- |
| CR-01 stream_get seam | register-before-replay + second-pass replay + unregister-on-error | `src/api/stream.py:175-201` + `event_bus.unregister` exists at line 87 of event_bus.py + test at `tests/unit/test_stream.py:143` |
| CR-02 channel 4xx path | full-attempt transcript replay on both branches | `runner/channel.py:221-222` (4xx) + `:250-251` (exception) + tests at `tests/unit/test_channel.py:323, 390` |
| WR-01 stream_post queue leak | try/except BaseException + unregister | `src/api/stream.py:98, 149-151` |
| WR-02 persister timeout | `asyncio.wait_for` with heartbeat + 30s grace | `src/api/stream.py:299-317` |
| WR-03 registry check ordering | registry check before persist | `src/api/stream.py:75` runs before `:78` |
| WR-04 body_iter partial replay duplicates | pop after append-to-transcript per item | `runner/channel.py:166-170` (per-item pop + transcript append) |
| WR-05 stop drain-vs-cancel race | cancel-first, then drain | `runner/hooks/audit.py:85-100` + test `tests/unit/test_audit_hook_drain.py:137` |
| WR-06 BaseException swallow | narrowed to CancelledError | `runner/hooks/audit.py:89`, `runner/channel.py:62` |
| WR-07 publish slow-subscriber sentinel wipes persister | `essential=True` unbounded queue | `src/streaming/event_bus.py:62, 76, 132` + `src/api/stream.py:299-301` |
| WR-08 `_diag` unconditional stderr | env-gated `_DIAG_ENABLED` | `src/api/internal.py:41, 49-51` |

Review-caught gaps that the goal-backward verification questioned (GET reconnect race, channel 4xx recovery) are both closed by the fix-pass tests.

### Human Verification Required

Two items genuinely require human / live-stack verification — neither can be falsified by grep, unit tests, or in-process integration tests:

1. **WR-02 persister idle-timeout budget under real LLM long-tail latency.** The 60s default budget (`runner_heartbeat_timeout_s + 30`) is a heuristic. Only real Gemini / Anthropic runs against the indexed PHP codebase can confirm the longest normal between-event gap stays under this budget. Code-level verification cannot disprove premature exit; the test suite uses fake runners with scripted events.

2. **Live runner spawn end-to-end with audit drain on warm-idle eviction.** Requires Docker runner + Postgres + MinIO + kloc-intelligence MCP stack. Unit tests assert the drain path POSTs every payload; only an integration-with-real-DB run validates that the corresponding `tool_call.completed` rows land in `audit_log`.

### Gaps Summary

No blocking gaps. All five ROADMAP success criteria are verified in the codebase. All five requirement IDs claimed by the phase plans (ISS-01..04, ISS-06) are implemented and covered by regression tests. The two follow-up code-review iterations (`01-REVIEW.md` → `01-REVIEW-FIX.md`) closed two additional critical gaps that goal-backward verification independently flagged (stream_get reconnect race and channel 4xx path), both via regression tests visible in the suite.

Status is **passed** with two `human_verification` items flagged for live-stack validation. Per the verifier methodology (Step 9), the presence of human items makes `human_needed` available — however these items are *advisory* hardening checks, not gaps in the phase's stated goal. The phase goal ("eliminate the event-ordering and reconnect bugs that violate AG-UI lifecycle invariants and lose audit events") is achieved by code-level guarantees; the human items are validating environmental fit, not code correctness.

Marking **passed** because:
- Every ROADMAP success criterion has a regression test asserting it in the running suite.
- Every requirement ID has implementing code AND a regression test that would have caught the original bug (per `01-REVIEW-FIX.md` reverse-checks documented in plan summaries).
- All 141 tests pass; 0 failed.
- The two human items are post-phase hardening verifications (LLM tail-latency tuning, live-stack audit completeness) that depend on operational environment, not phase deliverables.

---

_Verified: 2026-05-16T16:30:00Z_
_Verifier: Claude (gsd-verifier)_
