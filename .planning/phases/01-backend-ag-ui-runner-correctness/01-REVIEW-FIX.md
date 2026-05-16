---
phase: 01-backend-ag-ui-runner-correctness
fixed_at: 2026-05-16T15:10:00Z
review_path: .planning/phases/01-backend-ag-ui-runner-correctness/01-REVIEW.md
iteration: 1
findings_in_scope: 10
fixed: 10
skipped: 0
status: all_fixed
---

# Phase 01: Code Review Fix Report

**Fixed at:** 2026-05-16T15:10:00Z
**Source review:** .planning/phases/01-backend-ag-ui-runner-correctness/01-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 10 (2 critical + 8 warning; 4 info findings deferred per scope)
- Fixed: 10
- Skipped: 0
- Test suite: 107 unit + 34 integration pass (was 103 unit + 5 integration; +4 unit regression tests added, +29 integration tests have been added by parallel work and all pass).
- Commit chain: 7 atomic commits on `gsd-reviewfix/01-46776`, fast-forwarded to `master`.

## Fixed Issues

### CR-01: `stream_get` replay→live seam drops events on reconnect

**Files modified:** `src/api/stream.py`, `src/streaming/event_bus.py`, `tests/unit/test_stream.py`
**Commit:** `0e3fd8d2c`
**Applied fix:** The GET reconnect path now mirrors the POST path's subscribe-before-publish pattern. `event_bus.register` is called BEFORE the first `execution.replay_from` snapshot, and a second-pass replay over `replay_from(highest_seq)` covers anything appended to the ring while the subscriber was being registered. Added `event_bus.unregister` so the queue is reclaimed on the `BaseException` early-exit path. Regression test injects a SEAM event between register and replay snapshot and asserts the resumed client receives it.

### CR-02: `runner/channel.py` 4xx/5xx branch drops every frame except `last_inflight` (paired with WR-04)

**Files modified:** `runner/channel.py`, `tests/unit/test_channel.py`
**Commit:** `f2a1b6992`
**Applied fix:** Track all frames yielded during the current attempt in a fresh `yielded_this_attempt` list (recorded BEFORE the `yield` so consumer-side exceptions cannot lose the entry). Both recovery branches (`>= 400` status and `Exception`) prepend the FULL transcript via `pending_after_break[:0] = yielded_this_attempt` and then drain `_outbound` so the next attempt replays everything in order. Pop entries from `pending_after_break` before re-yielding (not via bulk `clear()`) so an interrupted replay leaves only the un-sent tail behind, eliminating the WR-04 partial-replay duplicate pattern as a side-effect. Two new regression tests: one asserts `[e1, e2, e3]` is preserved across a 503 mid-attempt; one asserts a `None` sentinel encountered during 4xx drain terminates the loop.

### WR-01: `stream_post` leaks a bus queue between `register` and `consume`

**Files modified:** `src/api/stream.py` (uses `event_bus.unregister` added in CR-01)
**Commit:** `6d0def8b0`
**Applied fix:** Wrap the body of `stream_post` between `event_bus.register` and `return make_response(...)` in `try / except BaseException: event_bus.unregister(...); raise`. Any error (saturated inbox, registry races, framework abandon, client disconnect before iteration) now reclaims the subscriber queue.

### WR-02: `_persist_events` holds asyncpg session forever if no terminal frame

**File modified:** `src/api/stream.py`
**Commit:** `e44ad6231`
**Status:** fixed: requires human verification
**Applied fix:** Wrap each `event_bus.subscribe` await in `asyncio.wait_for` with a budget of `runner_heartbeat_timeout_s + 30s` so the persister releases its asyncpg session when no event arrives within the heartbeat window. Emits a `stream.persister_idle_timeout` log warning on timeout. Verified syntactically and by re-running the existing stream test suite, but the chosen budget needs operator-side validation against real LLM completion gap distributions to confirm it cannot trip during normal long-tail traffic.

### WR-03: `_persist_user_message` runs before runner-registry health check

**File modified:** `src/api/stream.py`
**Commit:** `6d0def8b0`
**Applied fix:** Reordered: `_get_runner_registry(request)` now runs BEFORE `_persist_user_message`, so a registry-unavailable 503 fails the request without leaving a half-committed user-message row in Postgres. The Contract A invariant ("persist BEFORE forwarding to runner") still holds for the runner.inbox.put step that follows.

### WR-04: body_iter replay produces duplicate frames if reconnect during replay

**File modified:** `runner/channel.py`
**Commit:** `f2a1b6992` (folded into CR-02)
**Applied fix:** body_iter no longer pre-clears `pending_after_break`; each entry is popped after being recorded in `yielded_this_attempt`, so an exception mid-replay leaves only the un-sent tail in `pending_after_break`. The CR-02 recovery branches then prepend the attempt transcript to the surviving tail, producing exactly the right next-attempt sequence with no duplicates.

### WR-05: `AuditHookSender.stop()` drain-vs-cancel race could lose tool_call.completed

**Files modified:** `runner/hooks/audit.py`, `tests/unit/test_audit_hook_drain.py`
**Commit:** `818cf96cd`
**Applied fix:** Inverted the order: `_after_worker.cancel()` runs FIRST, then `await self._after_worker` waits for it to settle, then the drain runs under exclusive ownership of `_after_queue`. The worst case is now an at-most-one in-flight POST cancelled mid-await (irreducible minimum); previously a live worker could grab additional items between drain iterations and lose every one of them. New regression test installs a real worker, queues items, calls stop(), and asserts every `_post` saw the worker already-done.

### WR-06: `stop()` `except BaseException` swallows KeyboardInterrupt and SystemExit

**Files modified:** `runner/hooks/audit.py`, `runner/channel.py`
**Commit:** `818cf96cd`
**Applied fix:** Narrowed `except (asyncio.CancelledError, BaseException)` to `except asyncio.CancelledError` in both call sites. `KeyboardInterrupt` and `SystemExit` now propagate during shutdown, as intended.

### WR-07: `event_bus.publish` slow-subscriber sentinel can wipe the persister

**Files modified:** `src/streaming/event_bus.py`, `src/api/stream.py`
**Commit:** `e0043f665`
**Applied fix:** Added `essential=True` keyword on `event_bus.register` and `event_bus.subscribe`. Essential subscribers receive an unbounded `asyncio.Queue()` that the slow-subscriber sentinel branch cannot trigger on. The persister now subscribes with `essential=True`; SSE consumers keep the bounded 10k queue and the existing drop-on-saturation behaviour. Eliminates the DB-vs-SSE divergence the reviewer identified.

### WR-08: `_dispatch_frame` writes diagnostic frames to stderr on every event

**File modified:** `src/api/internal.py`
**Commit:** `91ed352ff`
**Applied fix:** Gated the `_diag` body behind a module-level `_DIAG_ENABLED = bool(os.environ.get("KLOC_DIAG_EVENTS"))`. Default off in production; opt-in for dev/CI diagnosis. Eliminates the per-frame `print(file=sys.stderr, flush=True)` sync I/O cost and stderr flood under load.

## Skipped Issues

None — all in-scope findings were fixed.

## Out-of-scope (info findings)

Per `fix_scope: critical_warning`, the 4 info findings (IN-01..IN-04) were not addressed in this iteration:

- **IN-01** (test ordering claim mismatch in `test_audit_hook_drain.py`): partially mooted — the new WR-05 ordering test (`test_stop_cancels_worker_before_draining`) does exercise the cancel-vs-drain order explicitly via a live worker.
- **IN-02** (`_channel_mod.asyncio.sleep` global mutation in `test_channel.py`): the two NEW CR-02 regression tests added in this fix iteration use `monkeypatch.setattr` (auto-restored on teardown). The pre-existing tests that mutate `asyncio.sleep` directly were not refactored to keep this change behaviour-neutral; an ISS-13 / sweep iteration can clean them up.
- **IN-03** (`_PRE_RUN_BUFFER_CAP` referenced before definition): unchanged.
- **IN-04** (`B-DIAG-X` / plan-section markers): explicitly part of the ISS-13 sweep per phase scope.

## Verification

- 107 unit tests pass (was 103; added 4 regression tests: CR-01, CR-02 ×2, WR-05).
- 34 integration tests pass; 4 pre-existing skips are unchanged (gated on phase-6 e2e wiring, unrelated to these fixes).
- Each fix was syntax-checked via `python -c "ast.parse(...)"` after the edit.
- Per-fix targeted test runs were green at commit time.
- Final full unit suite + integration suite re-run after the last commit (`91ed352ff`).

## Known caveats / human-verification items

- **WR-02 timeout budget**: `runner_heartbeat_timeout_s + 30s` is a heuristic. Confirm against production LLM tail-latency distributions before relying on it in production; if normal between-event gaps approach 60s, the persister will exit prematurely and a runner that DOES emit a terminal frame later will find no live persister to consume it. Mitigation if observed: raise the grace constant or replace with an explicit synthetic-RUN_ERROR publish from `RunnerRegistry._on_crash`.
- **WR-07 unbounded essential queue**: per-run essential queue is now uncapped. A persister that is fully stuck (DB completely unreachable for the run lifetime) accumulates events without bound. The WR-02 timeout escape limits the total exposure window. If memory pressure is observed under sustained DB outage, consider a high explicit cap (e.g., 100k) instead of fully unbounded.
- **CR-01 second-pass replay duplicates**: the replay/live seam fix can deliver the seam event twice in the worst case (once via replay, once via the queue). AG-UI intermediates are documented as idempotent by the phase plan (line 149 of channel.py), so this is acceptable; the SSE encoder validates per-event so duplicates pass through. If strict deduplication ever becomes a requirement, gate the second-pass replay on observed `highest_seq` change and dedupe against the queue head.

---

_Fixed: 2026-05-16T15:10:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
