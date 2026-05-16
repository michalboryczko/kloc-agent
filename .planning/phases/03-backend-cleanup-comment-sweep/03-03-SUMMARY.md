---
phase: 03
plan: 03
subsystem: backend
tags: [iss-10, perf, cache]
requires: []
provides: [registry_entry_is_alive_cache]
affects: [src/runner_mgmt/registry.py]
key_files_created: []
key_files_modified:
  - src/runner_mgmt/registry.py
decisions:
  - "TTL constant set to 50 ms (`_IS_ALIVE_TTL_S = 0.05`) — context budget; long enough to absorb a hot reuse loop, short enough that a freshly-killed container is noticed on the next cycle."
  - "Cache stored on `RegistryEntry`, not on the Runner Protocol. Protocol stays single-method."
  - "Used `time.monotonic()` (immune to wall-clock jumps)."
metrics:
  duration_minutes: ~7
  completed: 2026-05-16
---

# Phase 03 Plan 03: TTL-cached `RegistryEntry.is_alive`

Added `RegistryEntry.is_alive(runner)` that wraps `runner.is_alive(self.handle)`
behind a 50 ms TTL cache. Two call sites in `RunnerRegistry.get_or_spawn`
(post-kill-wait revalidation and spawn-lock double-check) now reuse the
cached liveness instead of fanning out to the Docker daemon.

## Deviations from Plan

None.

## Verification

`uv run --frozen pytest tests/unit/test_registry.py tests/unit/test_registry_concurrent_spawn.py tests/unit/test_warm_idle.py` — 26 passed.

## Self-Check: PASSED

- `_IS_ALIVE_TTL_S` constant in registry.py — FOUND
- `RegistryEntry.is_alive` method defined — FOUND
- Both `self._runner.is_alive(...)` call sites replaced — FOUND
