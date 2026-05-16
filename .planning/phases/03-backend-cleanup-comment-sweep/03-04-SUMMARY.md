---
phase: 03
plan: 04
subsystem: backend
tags: [iss-11, bug-fix, http-status]
requires: []
provides: [internal_disconnect_499_204]
affects: [src/api/internal.py]
key_files_created: []
key_files_modified:
  - src/api/internal.py
  - tests/unit/test_internal.py
decisions:
  - "499 (nginx 'client closed request') for empty-body disconnects — distinguishes connect-and-drop from accepted-partial."
  - "204 (no content) for partial-progress — frames already dispatched; nothing left to say."
  - "Replaced the prior `202` + JSON body with a plain `Response(status_code=...)`; the response body was unused by the runner channel."
  - "Unit test uses a fake Request whose `stream()` raises ClientDisconnect, avoiding TestClient and ASGI plumbing."
metrics:
  duration_minutes: ~8
  completed: 2026-05-16
---

# Phase 03 Plan 04: ClientDisconnect 499 / 204 distinction

The JSONL ingress endpoint previously returned `202 Accepted` for every
`ClientDisconnect`, indistinguishable from a normal-close. Two new branches:

- `count == 0` → `Response(499)` — runner connected and dropped without
  sending any frame. Operationally a signal that something is wrong upstream
  of the JSONL transport.
- `count > 0` → `Response(204)` — frames already dispatched, channel
  half-closed; the runner will reopen.

## Deviations from Plan

None.

## Verification

`uv run --frozen pytest tests/unit/test_internal.py` — 11 passed (2 new).

## Self-Check: PASSED

- `Response(status_code=499)` in src/api/internal.py — FOUND
- `status.HTTP_204_NO_CONTENT` ClientDisconnect branch — FOUND
- `test_client_disconnect_returns_499_when_no_frames` — FOUND
- `test_client_disconnect_returns_204_when_some_frames` — FOUND
