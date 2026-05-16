---
phase: 03
plan: 02
subsystem: backend
tags: [iss-09, cleanup]
requires: []
provides: []
affects: [src/main.py]
key_files_created: []
key_files_modified:
  - src/main.py
decisions:
  - "Plain assignment instead of AppState dataclass — that's a refactor, not a cleanup."
metrics:
  duration_minutes: ~3
  completed: 2026-05-16
---

# Phase 03 Plan 02: Delete unused annotated assignments

Two attribute-target annotations on `app.state` (`active_run_by_session`,
`pending_pre_run_started`) were no-op noise: Python does not enforce them
and `app.state` is a `starlette.datastructures.State` whose attribute type
is not validated. Replaced both with plain assignments.

## Deviations from Plan

None.

## Verification

`uv run --frozen pytest tests/unit/test_internal.py tests/unit/test_lifespan_boot.py` — 10 passed.

## Self-Check: PASSED

- src/main.py no longer has `: dict[str` annotations on app.state — VERIFIED
