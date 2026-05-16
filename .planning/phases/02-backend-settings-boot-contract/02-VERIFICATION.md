---
phase: 02-backend-settings-boot-contract
verified: 2026-05-16T00:00:00Z
status: human_needed
score: 11/11 must-haves verified
phase_req_ids: [ISS-05, ISS-07, ISS-12]
tests_passed: 166
tests_skipped: 5
tests_failed: 0
must_haves_verified: 11
must_haves_total: 11
overrides_applied: 0
human_verification:
  - test: "Boot backend with LLM_PROVIDER=anthropic and no ANTHROPIC_API_KEY set"
    expected: "uvicorn exits at startup with a pydantic ValidationError naming ANTHROPIC_API_KEY (or KLOC_STUB_MODE remediation). Does NOT reach the runner."
    why_human: "Validator is exercised in unit tests, but the full uvicorn boot-from-env path with a real misconfigured provider key is a production-shaped run that grep + unit tests cannot exercise."
  - test: "Boot backend without /var/run/docker.sock bind-mount (or with Docker daemon stopped)"
    expected: "Lifespan startup aborts loudly with the underlying aiodocker / DockerError traceback; uvicorn exits non-zero."
    why_human: "test_lifespan_aborts_when_docker_runner_construction_fails pins the ImportError path via monkeypatching aiodocker=None, but the real daemon-unreachable path (DockerError, socket missing) only surfaces when running the actual uvicorn process against a broken Docker environment."
  - test: "Boot backend with KLOC_HOOK_SECRET=dev-secret-please-rotate, ALLOW_HMAC_FALLBACK=true, KLOC_STUB_MODE unset"
    expected: "Boot aborts with a ValidationError whose message names both KLOC_HOOK_SECRET and KLOC_STUB_MODE remediations."
    why_human: "Validator predicate + error message verified by unit tests; the full uvicorn boot-from-env path against a production-shaped misconfiguration is a manual confidence check."
  - test: "make e2e-up (or compose up) with Docker available"
    expected: "Backend boots cleanly; session create + post message + SSE stream still work end-to-end after the lifespan collapse."
    why_human: "The phase removed a branch in the lifespan startup; only an end-to-end happy-path run confirms no regression in normal operation."
---

# Phase 02: Backend settings & boot contract — Verification Report

**Phase Goal:** Make misconfiguration surface at boot, never inside the runner. Remove the silent-degradation `stub` runner mode. Ensure HMAC fallback cannot use the placeholder secret in production.

**Verified:** 2026-05-16
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Goal-Backward)

The phase goal decomposes into three requirement bundles. Each observable truth is mapped to an artifact and evidence in the codebase.

| #   | Truth                                                                                                                                                       | Req     | Status     | Evidence                                                                                                                                 |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `src/api/stream.py` no longer reads `LLM_PROVIDER` / `LLM_MODEL_ID` from `os.environ`; both values come from `Settings`.                                    | ISS-05  | ✓ VERIFIED | `grep -nE "os\.environ\|LLM_PROVIDER\|LLM_MODEL_ID" src/api/stream.py` → no matches. `src/api/stream.py:424-425` reads from settings.   |
| 2   | `Settings` exposes `llm_model_id` (`str`, default `""`) and resolves to provider-appropriate default in `_validate_provider_key`.                            | ISS-05  | ✓ VERIFIED | `src/settings.py:67-75` declares field; lines 179-190 resolve default; tests `test_llm_model_id_defaults_to_*` and `_env_override_wins` |
| 3   | Booting with a configured provider but missing API key raises `ValueError`/`ValidationError` at `Settings()` construction.                                  | ISS-05  | ✓ VERIFIED | `src/settings.py:149-159`; tests `test_missing_anthropic_key_raises_when_not_stub`, `test_gemini_branch_enforced` PASS                  |
| 4   | `HydrationPayload` built by `_build_hydration_payload` uses `settings.llm_provider` / `settings.llm_model_id` verbatim.                                     | ISS-05  | ✓ VERIFIED | `src/api/stream.py:424-425, 433-434` shows direct settings assignment then `HydrationPayload(model_id=model_id, llm_provider=...)`     |
| 5   | Settings validator raises when `allow_hmac_fallback=True` AND `kloc_hook_secret == "dev-secret-please-rotate"` AND `stub_mode=False`.                       | ISS-07  | ✓ VERIFIED | `src/settings.py:167-177` — exact 3-AND predicate; tests `test_hmac_fallback_raises_when_default_secret_and_not_stub` + 8-cell truth table PASS |
| 6   | Validator allows the 7 other truth-table cells (stub-mode escape, rotated secret, fallback disabled).                                                       | ISS-07  | ✓ VERIFIED | `test_hmac_fallback_truth_table[*]` 8 parametrized cases all PASS                                                                       |
| 7   | Error message names both `KLOC_HOOK_SECRET` and `KLOC_STUB_MODE` so operator sees both remediations.                                                        | ISS-07  | ✓ VERIFIED | `src/settings.py:172-177` literal mentions both; `test_hmac_fallback_error_message_names_both_env_vars` PASS                            |
| 8   | `kloc_runner_mode` field removed from `Settings`; `grep` returns no production references.                                                                  | ISS-12  | ✓ VERIFIED | `grep -rE "kloc_runner_mode\|KLOC_RUNNER_MODE" src/ .env.example docker-compose.yml` → no matches. Test references in `tests/unit/test_settings.py` only assert absence. |
| 9   | `src/main.py` lifespan constructs `DockerRunner` unconditionally — no `if settings.kloc_runner_mode` branch, no `try/except` wrapping construction.         | ISS-12  | ✓ VERIFIED | `src/main.py:97-111` — single block with comment explaining why; `grep -c "DockerRunner(" src/main.py` = 1                              |
| 10  | When `DockerRunner` construction raises (e.g., `aiodocker` missing), lifespan re-raises; boot fails loudly.                                                  | ISS-12  | ✓ VERIFIED | `tests/unit/test_lifespan_boot.py::test_lifespan_aborts_when_docker_runner_construction_fails` PASSES, raises `RuntimeError("aiodocker")` |
| 11  | `.env.example` and `docker-compose.yml` no longer reference `KLOC_RUNNER_MODE`; tests previously using stub mode (test_cors.py) migrated cleanly.            | ISS-12  | ✓ VERIFIED | `.env.example` and `docker-compose.yml` grep clean; `tests/unit/test_cors.py:32-43` uses module-scope `MonkeyPatch` fixture (no module-level `os.environ.setdefault`) |

**Score:** 11/11 truths verified

### Required Artifacts

| Artifact                                       | Expected                                                                              | Status     | Details                                                                                                                                  |
| ---------------------------------------------- | ------------------------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `src/settings.py`                              | `llm_model_id`, HMAC 3-AND validator, no `kloc_runner_mode`                           | ✓ VERIFIED | Field declared (line 67-75); validator at 144-191 contains both ISS-05 model resolution + ISS-07 3-AND check; `kloc_runner_mode` absent |
| `src/api/stream.py`                            | Hydration built from settings; no `os.environ.get(LLM_*)` reads; no dead getattr     | ✓ VERIFIED | Lines 418, 424-425: direct settings reads; `mcp_url = settings.kloc_mcp_url` (WR-01 fix landed at commit 56b62400b)                     |
| `src/main.py`                                  | Single unconditional `DockerRunner(...)` site; no `if settings.kloc_runner_mode`     | ✓ VERIFIED | Line 102-111: import + DockerRunner construction; no branching; comment explains "Tests inject a fake Runner via RunnerRegistry.set_runner()" |
| `.env.example`                                 | No `KLOC_RUNNER_MODE` line                                                            | ✓ VERIFIED | grep returns nothing                                                                                                                     |
| `docker-compose.yml`                           | No `KLOC_RUNNER_MODE` environment key                                                 | ✓ VERIFIED | grep returns nothing                                                                                                                     |
| `tests/unit/test_settings.py`                  | Regression tests for ISS-05, ISS-07 truth table, ISS-12 field-removal                 | ✓ VERIFIED | 22 test functions including 8-cell parametrized truth table; all PASS                                                                    |
| `tests/unit/test_webhooks_hmac_fallback.py`    | Pin tests for `_build_app` construction + negative case using `monkeypatch`           | ✓ VERIFIED | 4 tests; both pin tests present and use `monkeypatch` (commit `a2aa6fa69`)                                                              |
| `tests/unit/test_cors.py`                      | No `KLOC_RUNNER_MODE` setenv; module-scope `MonkeyPatch` fixture for `KLOC_STUB_MODE` | ✓ VERIFIED | Module-scope autouse fixture at lines 32-43; `from src.main import app` deferred into `client()` fixture                                |
| `tests/unit/test_lifespan_boot.py`             | Boot-failure-on-DockerRunner-construction regression (WR-03)                          | ✓ VERIFIED | New file; single async test passes; monkey-patches `aiodocker` to `None` and asserts lifespan raises `RuntimeError`                      |

### Key Link Verification

| From                                                       | To                                                              | Via                                                                                | Status   | Details                                                                          |
| ---------------------------------------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------- |
| `src/api/stream.py:_build_hydration_payload`               | `src/settings.py:Settings.llm_provider/.llm_model_id`           | `settings.llm_provider`, `settings.llm_model_id` direct attribute reads            | ✓ WIRED  | Lines 424-425 of stream.py                                                       |
| `src/settings.py:_validate_provider_key` body              | `Settings.allow_hmac_fallback / .kloc_hook_secret / .stub_mode` | `model_validator(mode='after')` 3-AND predicate                                    | ✓ WIRED  | Lines 167-177; predicate matches contract exactly                                |
| `src/main.py:lifespan`                                     | `DockerRunner` constructor                                      | unconditional construction; ImportError / Exception aborts boot                    | ✓ WIRED  | Lines 102-111; no try/except wraps the construction                              |
| `tests/unit/test_lifespan_boot.py`                         | `RunnerRegistry.set_runner` semantic (boot-fails-loudly seam)   | monkeypatch `aiodocker=None` → lifespan raises                                     | ✓ WIRED  | Test passes; pins the contract                                                   |
| `tests/unit/test_cors.py`                                  | `Settings` boot validation bypass                               | module-scope `MonkeyPatch` fixture sets `KLOC_STUB_MODE=true` and `mp.undo()`      | ✓ WIRED  | No env pollution into downstream test modules                                    |

### Data-Flow Trace (Level 4)

Phase 2 produces configuration plumbing, not rendered user-facing data. Data-flow trace is not applicable for the artifacts in this phase — every artifact is either a config-validation predicate, a boot-lifecycle step, or a test. The closest analogue ("does `settings.llm_model_id` actually carry a non-empty string into `HydrationPayload`?") is covered by:

| Artifact                                  | Data Variable          | Source                                                            | Produces Real Data | Status      |
| ----------------------------------------- | ---------------------- | ----------------------------------------------------------------- | ------------------ | ----------- |
| `src/api/stream.py:_build_hydration_payload` | `model_id`, `llm_provider` | `settings.llm_provider` / `settings.llm_model_id` (resolved in validator) | Yes — never empty after construction (WR-02 fix; non-empty `str` invariant) | ✓ FLOWING   |

### Behavioral Spot-Checks

Phase 2 is configuration / boot-validation work. The relevant behaviors are exercised by the test suite (run below) rather than ad-hoc spot-check commands.

| Behavior                                                       | Command                                                                                                            | Result                       | Status   |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------------------------- | -------- |
| Phase-targeted tests pass                                      | `uv run pytest tests/unit/test_settings.py tests/unit/test_webhooks_hmac_fallback.py tests/unit/test_cors.py tests/unit/test_lifespan_boot.py -v` | 34 passed in 0.65s           | ✓ PASS   |
| Full unit + integration suite passes (no regressions)          | `uv run pytest tests/unit tests/integration -q`                                                                    | 166 passed, 5 skipped, 5.47s | ✓ PASS   |
| No `os.environ.get` for LLM_* in stream.py                     | `grep -nE "os\.environ\|LLM_PROVIDER\|LLM_MODEL_ID" src/api/stream.py`                                             | (no output)                  | ✓ PASS   |
| No `kloc_runner_mode` in production sources                    | `grep -rE "kloc_runner_mode\|KLOC_RUNNER_MODE" src/ .env.example docker-compose.yml`                               | (no output)                  | ✓ PASS   |
| Exactly one `DockerRunner(` site in src/main.py                | `grep -c "DockerRunner(" src/main.py`                                                                              | 1                            | ✓ PASS   |
| Phase commits exist in git log                                 | `git log --oneline` for b4d77936d, f35970d9a, eeb520ab3, e064be4f0, 084a5fe11, 49228e0f5, a2aa6fa69, plus 6 fix commits | all present                  | ✓ PASS   |

### Probe Execution

| Probe       | Command | Result | Status            |
| ----------- | ------- | ------ | ----------------- |
| (none declared by phase) | n/a | n/a | n/a — Phase 2 has no scripts/*/tests/probe-*.sh; the executor's verification gate is `uv run pytest`. |

### Requirements Coverage

| Requirement | Source Plan       | Description                                                                                                                                                                                                                                            | Status      | Evidence                                                                                                                                              |
| ----------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| ISS-05      | 02-01-PLAN.md     | `llm_provider` and `llm_model_id` always route through `Settings`; `os.environ.get` reads in `src/api/stream.py` are removed; missing provider key fails at boot, not in the runner                                                                    | ✓ SATISFIED | Truths 1-4 above; tests in `tests/unit/test_settings.py:122-183` + existing `_validate_provider_key` regression tests                                 |
| ISS-07      | 02-02-PLAN.md     | Settings validator raises when `allow_hmac_fallback=True` and `kloc_hook_secret == "dev-secret-please-rotate"` and not `stub_mode`                                                                                                                     | ✓ SATISFIED | Truths 5-7 above; `src/settings.py:167-177`; 8-cell parametrized truth table + dedicated raise/allow/error-message tests                              |
| ISS-12      | 02-03-PLAN.md     | `kloc_runner_mode` removed from `Settings` and `.env.example`; lifespan unconditionally constructs `DockerRunner`; `ImportError`/construction failure fails boot loudly; tests previously using `KLOC_RUNNER_MODE=stub` use `RunnerRegistry.set_runner()` instead | ✓ SATISFIED | Truths 8-11 above; `src/main.py:97-111`; `tests/unit/test_lifespan_boot.py`; `tests/unit/test_cors.py` migration; 3 regression tests in `test_settings.py` |

No ORPHANED requirements detected. REQUIREMENTS.md maps exactly ISS-05/07/12 to Phase 2 and all three are covered.

### Anti-Patterns Found

Anti-pattern scan on the modified files. The Phase 02-REVIEW already itemized 5 Info findings (IN-01..05) that violate the comment policy in `CLAUDE.md`. The REVIEW-FIX explicitly deferred IN-01..03 to Phase 3 (which is the scoped "comment sweep" phase per ROADMAP.md). IN-04 and IN-05 were deferred as hygiene (the manual `git grep` for IN-05 confirmed no production references remain). Per the ROADMAP, the comment-policy cleanup is intentionally batched into Phase 3, so these violations are not Phase 2 gaps.

| File                                      | Line(s)            | Pattern                                                                                                              | Severity   | Impact                                                                                                                                          |
| ----------------------------------------- | ------------------ | -------------------------------------------------------------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/settings.py`                         | 51, 146-147, 161-165 | "dev-2 CR" reviewer-ID; history narration; "ISS-07:" issue-ID prefix in comment                                       | ℹ️ Info    | Comment-policy violations explicitly deferred to Phase 3 (ROADMAP.md SC-5); does not affect behavior or goal achievement                       |
| `src/main.py`                             | 1-13, 70-87, 114, 125-134 | Docstring naming "dev-2", "Phase 1.A7", "(AC25)", "(AC24)"; "B-INFRA-DISPATCH:" tag; log string naming "dev-2 Phase 2.D9" | ℹ️ Info    | Comment-policy violations explicitly deferred to Phase 3 (ROADMAP.md SC-5); does not affect behavior or goal achievement                       |
| `src/api/stream.py`                       | 1, 8, 54, 71-74, 76, 107-114, 282-298 | "dev-2-owned half of Track C"; "(AC5, AC18)"; "Contract A invariant"; review-round IDs "WR-02/WR-07"; "ISS-02:" prefix | ℹ️ Info    | Comment-policy violations explicitly deferred to Phase 3 (ROADMAP.md SC-5); does not affect behavior or goal achievement                       |
| `tests/unit/test_webhooks_hmac_fallback.py` | 101, 114             | Direct `webhooks.get_settings = _patched` mutation (IN-04)                                                            | ℹ️ Info    | Auto-restored by autouse fixture; deferred as hygiene per REVIEW-FIX                                                                            |

No 🛑 Blocker or ⚠️ Warning anti-patterns introduced by Phase 2 code. The "TODO/FIXME/XXX/HACK" debt-marker scan against `src/settings.py`, `src/main.py`, `src/api/stream.py`, and the affected test files returned no production-code debt markers (the strings appear only in pre-existing log/comment context unrelated to this phase's changes).

### Human Verification Required

Four items need an operator's manual hands-on check. All four are production-shaped boot scenarios that grep + unit tests cannot exercise:

#### 1. Provider-key boot failure (real env)

**Test:** Run `uvicorn src.main:app` with `LLM_PROVIDER=anthropic` and no `ANTHROPIC_API_KEY`.
**Expected:** Process exits at startup with a pydantic `ValidationError` whose message names `ANTHROPIC_API_KEY` and the `KLOC_STUB_MODE=true` remediation. The error must come from `_validate_provider_key`, not from anywhere inside the runner.
**Why human:** Unit tests cover the validator predicate; only a real boot exercises the full uvicorn-from-env path the operator hits in production.

#### 2. Docker-unreachable boot failure (real daemon)

**Test:** Run the backend (or `make e2e-up`) on a host without the `/var/run/docker.sock` bind-mount (or with the Docker daemon stopped).
**Expected:** Lifespan startup aborts loudly with an `aiodocker` ImportError or `DockerError` traceback; uvicorn exits non-zero. No silent fallback.
**Why human:** `test_lifespan_aborts_when_docker_runner_construction_fails` pins the `aiodocker=None` ImportError path via monkeypatch; the production-shaped "daemon unreachable" path needs a real broken Docker environment.

#### 3. HMAC fallback + default-secret boot failure (real env)

**Test:** Run `uvicorn src.main:app` with `ALLOW_HMAC_FALLBACK=true`, `KLOC_HOOK_SECRET=dev-secret-please-rotate`, and `KLOC_STUB_MODE` unset.
**Expected:** Boot aborts with a ValidationError whose message names both `KLOC_HOOK_SECRET` (rotate) and `KLOC_STUB_MODE` (bypass).
**Why human:** Validator predicate + error-message string verified by unit tests; production-shaped uvicorn boot against a misconfiguration is a manual confidence check.

#### 4. End-to-end happy path still boots

**Test:** Run `make e2e-up` (or `docker compose up`) with Docker available, a real provider key set, and a non-default `KLOC_HOOK_SECRET`.
**Expected:** Backend boots cleanly to `Application startup complete.`; session create + post message + SSE stream all still work end-to-end. No regression introduced by collapsing the lifespan branch.
**Why human:** The phase removed a branch in lifespan startup; only an end-to-end run confirms no regression on the normal operator path.

### Gaps Summary

None. All 11 must-have truths are verified in the codebase with concrete evidence; the full 166-test unit + integration suite passes; the four open items are explicitly production-shaped boot scenarios that require an operator's hands-on confidence check and are routed to human verification (which is the intended Escalation Gate pattern for this phase). The 5 comment-policy Info findings from `02-REVIEW.md` (IN-01..05) are explicitly scheduled for Phase 3 (`Backend cleanup & comment sweep`) per ROADMAP.md success criterion 5 of that phase and the REVIEW-FIX explicit "Deferred" section.

---

_Verified: 2026-05-16_
_Verifier: Claude (gsd-verifier)_
