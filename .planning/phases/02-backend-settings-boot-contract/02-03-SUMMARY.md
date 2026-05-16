---
phase: 02-backend-settings-boot-contract
plan: 03
subsystem: backend-settings
tags: [iss-12, runner-mode, lifespan, boot-validation]
requirements: [ISS-12]
dependency-graph:
  requires: ["02-02"]
  provides: ["Unconditional DockerRunner construction at boot; stub mode removed"]
  affects: ["src/main.py:lifespan", "Settings field surface", ".env.example", "docker-compose.yml"]
tech-stack:
  added: []
  patterns: ["fail-loudly at boot for runner construction"]
key-files:
  created: []
  modified:
    - src/settings.py
    - src/main.py
    - .env.example
    - docker-compose.yml
    - tests/unit/test_cors.py
    - tests/unit/test_settings.py
    - tests/unit/test_webhooks_hmac_fallback.py
decisions:
  - "Lifespan re-raises any `DockerRunner` construction failure (ImportError, DockerError, daemon unreachable). No try/except."
  - "test_cors.py deletes the stale `KLOC_RUNNER_MODE=stub` setenv but keeps `KLOC_STUB_MODE=true` (different concern — provider-key bypass)."
  - "Did NOT migrate test_cors.py to `set_runner()` injection. The file already uses `TestClient` without `with ... as`, so lifespan never runs — the setenv was dead code; deletion is the right migration."
  - "Tests previously relying on stub mode (only `test_cors.py` in the codebase) needed no `set_runner()` migration — see above."
  - "Strings `kloc_runner_mode` and `KLOC_RUNNER_MODE` still appear in `tests/unit/test_settings.py` because the 3 regression tests pin that the field is gone. Mentioning the removed name is required to assert its absence — this is the documented exception to the success-criteria grep."
metrics:
  duration: ~10 min
  completed: 2026-05-16
---

# Phase 02 Plan 03: Stub runner mode removed (ISS-12) Summary

One-liner: `kloc_runner_mode` deleted from Settings, `.env.example`, and `docker-compose.yml`; `src/main.py` lifespan unconditionally constructs `DockerRunner` and lets any construction failure abort boot.

## What Shipped

**Task 1 (commit 084a5fe11):**
- `src/settings.py`: removed the `kloc_runner_mode: Literal["docker", "stub"]` field and its B-INFRA-1 comment.
- `.env.example`: removed the `KLOC_RUNNER_MODE=docker` line + the 4-line comment block above it.
- `docker-compose.yml`: removed the `KLOC_RUNNER_MODE: ${KLOC_RUNNER_MODE:-docker}` env entry + comment.
- `tests/unit/test_settings.py`: 3 regression tests pin (a) field gone from `Settings.model_fields`, (b) a stale `KLOC_RUNNER_MODE` env var is silently dropped by Pydantic's `extra="ignore"`, (c) `.env.example` no longer documents the key.

**Task 2 (commit 49228e0f5):**
- `src/main.py`: replaced the `if settings.kloc_runner_mode == "docker": ... else: try/except` block (lines 103-131) with a single unconditional `DockerRunner(...)` construction. ImportError or any other failure propagates and aborts boot. Inline comment explains the *why* (silent fallback masked the `/var/run/docker.sock` bind-mount bug; tests inject via `RunnerRegistry.set_runner()`).
- `src/main.py`: trimmed the RunnerRegistry comment block to remove the stale "Phase-2.0-stub mode" mention.
- `tests/unit/test_cors.py`: deleted `os.environ.setdefault("KLOC_RUNNER_MODE", "stub")`. The `KLOC_STUB_MODE=true` setenv stays (different concern). Tests pass because `TestClient` is used without `with ... as`, so the lifespan never ran — the setenv was already dead code.

## Verification

- `uv run pytest tests/unit -x -q` — 124 passed, 1 skipped (unit suite).
- `uv run pytest tests/unit tests/integration -q` — 155 passed, 5 skipped (no errors).
- `grep -E "kloc_runner_mode|KLOC_RUNNER_MODE" src/settings.py .env.example docker-compose.yml src/main.py tests/unit/test_cors.py` — returns nothing.
- `grep -c "DockerRunner(" src/main.py` — returns `1`.
- The strings `kloc_runner_mode` / `KLOC_RUNNER_MODE` survive in `tests/unit/test_settings.py` because the 3 new regression tests must mention the removed name to assert its absence. This is the documented and intentional exception.

## Deviations from Plan

**[Rule 1 - Bug] HMAC pin tests in 02-02 polluted `os.environ`**
- **Found during:** running the combined `tests/unit tests/integration` suite at the end of plan 02-03.
- **Issue:** The two pin tests added in commit `e064be4f0` (plan 02-02 Task 2) mutated `os.environ` directly inside a `try/finally`. When the integration test session inherited `KLOC_STUB_MODE` from `tests/unit/test_cors.py` (which sets it via `os.environ.setdefault` at import time), the pin test's `os.environ.pop("KLOC_STUB_MODE", None)` permanently removed it for every subsequent test module. Integration fixtures in `tests/integration/test_sessions_api.py` and `test_stream_reconnect.py` then tripped `_validate_provider_key` when constructing `Settings()` without a real provider key. 9 integration test setups errored.
- **Fix:** Switched both pin tests to `monkeypatch.setenv` / `monkeypatch.delenv`. Pytest's `monkeypatch` reverts every env mutation at function teardown.
- **Files modified:** `tests/unit/test_webhooks_hmac_fallback.py`
- **Commit:** `a2aa6fa69` (`fix(02-02): use monkeypatch in HMAC pin tests to avoid env pollution`). Committed under the 02-02 prefix because the bug is in code introduced by plan 02-02, not 02-03; the user-facing behaviour of plan 02-02 is unchanged.

**Pre-existing e2e test failure (not addressed)**
- `tests/e2e/test_artifact_lifecycle.py::test_artifact_webhook_first_call_returns_202` fails with 401 "unknown runner". Verified pre-existing by checking the failure reproduces against the c3c4ef0d2 baseline (the commit before Phase 2 started). Out of scope per the executor's scope-boundary rule; logged here for visibility.

## Threat Flags

None — removing a field tightens the trust boundary (operator env → backend boot). `T-02-05` (boot fails when daemon unreachable) is now the intended behaviour. `T-02-06` (stale env var) is mitigated by the regression test. `T-02-07` (test seam via `set_runner()`) was not exercised because `test_cors.py` did not need it.

## Self-Check: PASSED

- `src/settings.py` — exists; `kloc_runner_mode` field is gone; `Literal` import still used for `LlmProvider`.
- `src/main.py` — exists; single `DockerRunner(` construction site; no `if settings.kloc_runner_mode` branch; comment update applied.
- `.env.example` — exists; no `KLOC_RUNNER_MODE` line.
- `docker-compose.yml` — exists; no `KLOC_RUNNER_MODE` entry; the `/var/run/docker.sock` bind-mount comment is updated.
- `tests/unit/test_cors.py` — exists; only `KLOC_STUB_MODE` env-var setdefault remains.
- `tests/unit/test_settings.py` — exists; contains 3 new `test_*kloc_runner_mode*` and `test_env_example_*` functions.
- `tests/unit/test_webhooks_hmac_fallback.py` — exists; pin tests use `monkeypatch`.
- Commits `084a5fe11`, `49228e0f5`, `a2aa6fa69` all exist in git log.
