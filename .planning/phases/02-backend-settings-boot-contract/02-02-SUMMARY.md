---
phase: 02-backend-settings-boot-contract
plan: 02
subsystem: backend-settings
tags: [iss-07, settings, hmac, security]
requirements: [ISS-07]
dependency-graph:
  requires: ["02-01"]
  provides: ["3-AND HMAC fallback boot-time check in Settings"]
  affects: ["src/api/webhooks.py (boot-only — behaviour unchanged)"]
tech-stack:
  added: []
  patterns: ["3-AND predicate in model_validator(mode='after')"]
key-files:
  created: []
  modified:
    - src/settings.py
    - tests/unit/test_settings.py
    - tests/unit/test_webhooks_hmac_fallback.py
decisions:
  - "New 3-AND predicate lives in the existing `_validate_provider_key` body, not a sibling validator (readability)."
  - "Error message literal-strings `KLOC_HOOK_SECRET` and `KLOC_STUB_MODE` so the verification grep can pin both tokens."
  - "Tests use env-vars (not kwargs) to set `stub_mode` because the field has `validation_alias=\"KLOC_STUB_MODE\"` and `populate_by_name` is not configured — kwarg `stub_mode=True` silently no-ops."
metrics:
  duration: ~6 min
  completed: 2026-05-16
---

# Phase 02 Plan 02: HMAC fallback hardening (ISS-07) Summary

One-liner: `Settings` refuses to boot when `allow_hmac_fallback=True`, the placeholder `kloc_hook_secret="dev-secret-please-rotate"` is unchanged, and `stub_mode` is off — closing the forge-any-runner-webhook path with a 3-AND predicate.

## What Shipped

**Task 1 (commit eeb520ab3):** Extended the existing `_validate_provider_key` validator with the 3-AND check. Error message names both `KLOC_HOOK_SECRET` (rotate it) and `KLOC_STUB_MODE` (bypass for test runs) so the operator sees both remediations. Added 5 truth-table tests covering: raise / stub-mode allows / rotated-secret allows / disabled-fallback ok / error-message naming.

**Task 2 (commit e064be4f0):** Added two pin tests to `tests/unit/test_webhooks_hmac_fallback.py`:
- Happy path: `_build_app`'s `Settings(BOOTSTRAP_SECRET, allow_hmac_fallback, stub_mode=True)` still constructs because the secret is non-default. If a future tightening of the validator would break `_build_app`, this catches it.
- Negative path: the exact combo the validator is designed to refuse (default secret, fallback on, no stub) raises `ValidationError / ValueError`.

## Verification

- `uv run pytest tests/unit/test_settings.py -x -q` — 14 passed (the 4 ISS-05 + 5 new ISS-07 + 5 pre-existing).
- `uv run pytest tests/unit/test_webhooks_hmac_fallback.py -x -q` — 4 passed (2 pre-existing + 2 new pins).
- `grep -nE "allow_hmac_fallback.*kloc_hook_secret|dev-secret-please-rotate" src/settings.py` — returns the new validator block.
- `grep -c "dev-secret-please-rotate" src/settings.py` — returns 2 (field default + validator check).
- `grep -c "def test_build_app_settings" tests/unit/test_webhooks_hmac_fallback.py` — returns 2.

## Deviations from Plan

**[Rule 3 - Blocking] Settings kwarg `stub_mode=True` does not bind**
- **Found during:** Task 1 first test run
- **Issue:** The plan's example test code passed `stub_mode=True` as a constructor kwarg, but `stub_mode` is declared with `validation_alias="KLOC_STUB_MODE"` and `Settings.model_config` does not set `populate_by_name`. Pydantic-settings therefore ignored the kwarg and the value resolved from the environment (falsy when `KLOC_STUB_MODE` is unset). The first test failed because the 3-AND predicate fired despite the kwarg.
- **Fix:** All new tests use `monkeypatch.setenv("KLOC_STUB_MODE", "true")` / `delenv` to control `stub_mode`. Same pattern matches the existing `test_api_keys_default_to_none` / `test_missing_anthropic_key_allowed_when_stub` style. The two pin tests in `test_webhooks_hmac_fallback.py` (which already imports `os` indirectly) use `os.environ` directly inside a `try/finally` because `monkeypatch` isn't available without making them async or pulling a fixture.
- **Files modified:** `tests/unit/test_settings.py`, `tests/unit/test_webhooks_hmac_fallback.py`
- **Commits:** Folded into the Task 1 and Task 2 commits (not separate commits — discovered before the first commit landed).

This deviation is mechanical and doesn't change the truth-table coverage — every plan-specified branch is still tested.

## Threat Flags

None — the validator addition is purely a boot-time refusal. No new endpoints, no schema change. `T-02-03` (forge-webhooks via placeholder secret) is now mitigated by boot-time refusal; the pin tests on the webhook side document the contract.

## Self-Check: PASSED

- `src/settings.py` — exists; the 3-AND predicate block exists immediately after the provider-key checks; the new `llm_model_id` resolution block follows it.
- `tests/unit/test_settings.py` — exists; contains all 5 `test_hmac_fallback_*` functions.
- `tests/unit/test_webhooks_hmac_fallback.py` — exists; contains 2 new `test_build_app_settings_*` functions.
- Commits `eeb520ab3` and `e064be4f0` exist in git log.
