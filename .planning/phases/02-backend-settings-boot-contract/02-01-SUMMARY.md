---
phase: 02-backend-settings-boot-contract
plan: 01
subsystem: backend-settings
tags: [iss-05, settings, boot-validation, stream]
requirements: [ISS-05]
dependency-graph:
  requires: []
  provides: ["Settings.llm_model_id field with provider-aware default"]
  affects: ["src/api/stream.py:_build_hydration_payload"]
tech-stack:
  added: []
  patterns: ["model_validator(mode='after') resolves sibling-dependent default"]
key-files:
  created: []
  modified:
    - src/settings.py
    - src/api/stream.py
    - tests/unit/test_settings.py
decisions:
  - "Resolve `llm_model_id` default inside `_validate_provider_key` (not a `Field(default_factory=...)`) because the default depends on `llm_provider`."
  - "Use `object.__setattr__` to set the resolved value; the model is already constructed when the validator runs."
  - "Drop `import os` from `src/api/stream.py` entirely — no remaining `os.*` calls in the file."
metrics:
  duration: ~5 min
  completed: 2026-05-16
---

# Phase 02 Plan 01: Provider+model routed through Settings (ISS-05) Summary

One-liner: `Settings.llm_model_id` field with provider-aware default; `src/api/stream.py:_build_hydration_payload` reads provider + model exclusively from Settings (no `os.environ.get` fallback).

## What Shipped

**Task 1 (commit b4d77936d):** Added `llm_model_id: str | None = Field(default=None, ...)` to `Settings`. The provider-aware default (`gemini-3.1-pro-preview` for gemini, `claude-3-5-haiku-20241022` for anthropic) is resolved at the bottom of the existing `_validate_provider_key` validator via `object.__setattr__` because the default depends on a sibling field (`llm_provider`). Three new tests pin the default / env-override truth table.

**Task 2 (commit f35970d9a):** Removed the `os.environ.get("LLM_PROVIDER")` and `os.environ.get("LLM_MODEL_ID")` reads in `_build_hydration_payload` (previously at `src/api/stream.py:432-438`). They are now `settings.llm_provider` and `settings.llm_model_id`. `import os` deleted (no other `os.*` use in the file). One pin test added to `tests/unit/test_settings.py`.

## Verification

- `uv run pytest tests/unit/test_settings.py -x -q` — 9 passed (4 pre-existing + 4 new + 1 pin).
- `uv run pytest tests/unit/test_settings.py tests/unit/test_stream.py -x -q` — 14 passed.
- `grep -cE "os\.environ\.get.*(LLM_PROVIDER|LLM_MODEL_ID)" src/api/stream.py` returns `0`.
- `grep -n "llm_model_id" src/settings.py` shows field declaration + resolution validator block.
- `grep -n "settings.llm_model_id" src/api/stream.py` shows the new assignment line.
- `grep -n "settings.llm_provider" src/api/stream.py` shows the new assignment line.

## Deviations from Plan

None — plan executed exactly as written. The action gave Claude discretion over whether to consolidate the resolution into `_validate_provider_key` or split it into a sibling validator. Chose to keep it in the existing validator because the body was short enough that adding a second validator would have hurt readability more than it helped separation of concerns.

## Threat Flags

None — no new network surface, auth path, or schema change. The plan's `T-02-01` (resolve default inside model_validator so re-validation produces the same default) and `T-02-02` (removed silent per-request provider override) are both implemented and covered by the new tests.

## Self-Check: PASSED

- `src/settings.py` — exists, contains `llm_model_id` field at line ~76 and resolution at the bottom of `_validate_provider_key`
- `src/api/stream.py` — exists, line 14 no longer has `import os`, lines 426-431 read from Settings only
- `tests/unit/test_settings.py` — exists, contains 4 new test functions (`test_llm_model_id_*` and `test_stream_call_site_uses_settings_only`)
- Commits `b4d77936d` and `f35970d9a` exist in git log.
