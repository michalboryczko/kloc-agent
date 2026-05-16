---
phase: 02-backend-settings-boot-contract
fixed_at: 2026-05-16T00:00:00Z
review_path: .planning/phases/02-backend-settings-boot-contract/02-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 02 (Backend Settings + Boot Contract): Code Review Fix Report

**Fixed at:** 2026-05-16
**Source review:** `.planning/phases/02-backend-settings-boot-contract/02-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (all 6 Warnings; the 5 Info findings are deferred — see "Deferred" below)
- Fixed: 6
- Skipped: 0
- Test suite: 166 passed, 5 skipped (baseline before fixes: 155 passed, 5 skipped — net +11 new tests; no regressions)

## Fixed Issues

### WR-01: `kloc_mcp_url` fallback in `_build_hydration_payload` is now dead code

**Files modified:** `src/api/stream.py`
**Commit:** 56b62400b
**Applied fix:** Replaced `getattr(settings, "kloc_mcp_url", "http://host.docker.internal:8765/mcp")` with the direct attribute access `settings.kloc_mcp_url`. Removed the stale "Fallback to that string literal until dev-1's settings field ships in parallel" comment (also mentioned in IN-03). The string-literal default already lives on `Settings.kloc_mcp_url` (`src/settings.py:78-89`); the `getattr` was unreachable and would mask a real misconfiguration if the field were ever renamed.

### WR-02: `Settings.llm_model_id` typed `str | None` but invariant is `str`

**Files modified:** `src/settings.py`
**Commit:** 14a96e128
**Applied fix:** Tightened the annotation from `str | None` to `str` with `default=""`. Switched the `_validate_provider_key` resolution check from `if self.llm_model_id is None:` to `if not self.llm_model_id:` so explicit empty-string env values still resolve to the provider-appropriate default. Type-checkers no longer demand redundant None-guards at the `HydrationPayload(model_id=settings.llm_model_id, ...)` call site in `src/api/stream.py`.

### WR-03: No regression test covering `DockerRunner` construction failure during lifespan

**Files modified:** `tests/unit/test_lifespan_boot.py` (new file)
**Commit:** a5f417881
**Applied fix:** Added `tests/unit/test_lifespan_boot.py::test_lifespan_aborts_when_docker_runner_construction_fails`. The test monkey-patches `src.runner_mgmt.docker_runner.aiodocker` to `None`, replaces the DB engine / aioboto3 Session / sweep_orphaned_messages with no-ops, then asserts that `async with main_mod.lifespan(app)` raises `RuntimeError` matching "aiodocker". A future refactor that wraps the `DockerRunner(...)` call in a permissive `except Exception` would silently start the app with a broken runner backend; this test makes that drift loud.

### WR-04: HMAC-fallback 3-AND truth table is only partially covered

**Files modified:** `tests/unit/test_settings.py`
**Commit:** b43f35765
**Applied fix:** Added `test_hmac_fallback_truth_table` parametrized over all 8 cells of `(allow_hmac_fallback, kloc_hook_secret, stub_mode)`. Only `(True, "dev-secret-please-rotate", False)` expects a raise; the other 7 cells must construct cleanly. The 4 previously-existing tests are retained for readability of the named happy / sad paths; the parametrized table is the exhaustive backstop.

### WR-05: No test for provider-key validator pass-through on `openrouter` / `bedrock`

**Files modified:** `tests/unit/test_settings.py`
**Commit:** bc576c16f
**Applied fix:** Added `test_openrouter_provider_does_not_require_key` and `test_bedrock_provider_does_not_require_key`. Both delete `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and `KLOC_STUB_MODE` from the env, set `LLM_PROVIDER` to the respective value, and assert `Settings(_env_file=None)` constructs cleanly with `stub_mode=False`. A future change that adds an `or self.llm_provider == "openrouter"` branch to the validator (without also adding a key field) would now be caught immediately.

### WR-06: Module-level `os.environ.setdefault` in `test_cors.py` leaks across the test session

**Files modified:** `tests/unit/test_cors.py`
**Commit:** 84c1e635b
**Applied fix:** Replaced the module-scope `os.environ.setdefault("KLOC_STUB_MODE", "true")` with an autouse module-scope fixture `_stub_mode_for_module` that uses `_pytest.monkeypatch.MonkeyPatch()` / `mp.setenv(...)` / `mp.undo()`. The fixture's `yield` boundary guarantees the env mutation is rolled back after the module's last test, so test-module order in CI no longer affects later tests' view of `KLOC_STUB_MODE`. The `from src.main import app` import was moved out of module scope into the `client` fixture so the import-time `Settings` read happens after the patched env is in place; `get_settings.cache_clear()` is called inside the fixture for the same reason.

## Deferred (out of scope for this fix run)

Per the orchestrator prompt, the 5 Info findings were not fixed in this iteration:

- **IN-01, IN-02, IN-03** — Comment-policy violations (people names, plan §, AC numbers, ISS tags, history narration) in `src/settings.py`, `src/main.py`, `src/api/stream.py`. Scoped to be swept in Phase 3 (project-wide comment cleanup).
- **IN-04** — `webhooks.get_settings = _patched` direct assignment in `tests/unit/test_webhooks_hmac_fallback.py`. The autouse `_reset_settings_cache` fixture already restores the symbol on test exit; the suggested `monkeypatch.setattr` refactor is hygiene, not a correctness fix.
- **IN-05** — Widening `test_env_example_has_no_kloc_runner_mode_entry` into a `git grep` across the repo. Hand-verified: `git grep KLOC_RUNNER_MODE` returns only `tests/unit/test_settings.py` (the test file itself); no production references remain.

One side-effect of WR-01: the IN-03 finding for `src/api/stream.py:421` (the `# Fallback to that string literal until dev-1's settings field ships in parallel.` comment) is incidentally fixed because the entire comment block was rewritten when the dead `getattr` was removed. The other IN-03 line targets (lines 1, 8, 54, 71-74, 76, 107-114, 282-289, 294-298, 426) remain for the Phase 3 sweep.

---

_Fixed: 2026-05-16_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
