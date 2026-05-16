---
phase: 03
plan: 01
subsystem: backend
tags: [iss-08, cleanup, settings]
requires: []
provides: [diag_events_setting]
affects: [src/settings.py, src/api/internal.py, src/api/webhooks.py]
key_files_created: []
key_files_modified:
  - src/settings.py
  - src/api/internal.py
  - src/api/webhooks.py
  - tests/unit/test_settings.py
decisions:
  - "Gate _diag through Settings.diag_events (single source of truth) instead of a module-level _DIAG_ENABLED constant read once from env at import time."
  - "stream.py has no _diag helper — alignment ask was a no-op there."
metrics:
  duration_minutes: ~10
  completed: 2026-05-16
---

# Phase 03 Plan 01: Gate `_diag` behind `Settings.diag_events`

`Settings` now owns a `diag_events: bool` field (alias `KLOC_DIAG_EVENTS`,
default `False`). Both `_diag` helpers in `src/api/internal.py` and
`src/api/webhooks.py` route through it. Production traffic no longer pays a
sync-stderr write per AG-UI event by default. Two regression tests pin the
default-off behaviour and the env-var opt-in.

## Notable

- `stream.py` does not have a `_diag` helper today — the Phase 3 context's
  "align stream.py to also route through Settings" instruction was a no-op
  there. Documented; no change made.
- `webhooks.py:_diag` previously always wrote regardless of env state; now
  honours the same gate as internal.py.

## Deviations from Plan

None — plan executed as written.

## Verification

`uv run --frozen pytest tests/unit/test_settings.py tests/unit/test_internal.py tests/unit/test_webhooks_hmac_fallback.py` — 42 passed.

## Self-Check: PASSED

- src/settings.py has `diag_events` field — FOUND
- src/api/internal.py `_diag` reads `get_settings().diag_events` — FOUND
- src/api/webhooks.py `_diag` reads `get_settings().diag_events` — FOUND
- tests/unit/test_settings.py has `test_diag_events_defaults_to_false` — FOUND
