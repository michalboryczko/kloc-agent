---
phase: 02-backend-settings-boot-contract
reviewed: 2026-05-16T00:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - src/settings.py
  - src/main.py
  - src/api/stream.py
  - tests/unit/test_settings.py
  - tests/unit/test_webhooks_hmac_fallback.py
  - tests/unit/test_cors.py
findings:
  critical: 0
  warning: 6
  info: 5
  total: 11
status: issues_found
---

# Phase 02 (Backend Settings + Boot Contract): Code Review Report

**Reviewed:** 2026-05-16
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

The two validators in `src/settings.py` implement their stated predicates correctly. The provider-key check fires exactly on `not stub_mode AND provider in {gemini,anthropic} AND not key`; the HMAC-fallback check fires exactly on `allow_hmac_fallback AND secret == "dev-secret-please-rotate" AND not stub_mode`. The `_build_hydration_payload` call site in `src/api/stream.py` has been scrubbed of `LLM_PROVIDER` / `LLM_MODEL_ID` `os.environ` reads — both now flow from `Settings` only. Boot in `src/main.py` constructs `DockerRunner` outside any `try/except`, so construction failures (missing `aiodocker`, daemon unreachable) do propagate and abort lifespan.

Findings are dominated by comment-policy violations (CLAUDE.md "no people/plan §/AC/reviewer-IDs/history"), test coverage gaps for the new validators (no OpenRouter/Bedrock passthrough test, no boot-fail-on-DockerRunner test, incomplete HMAC 3-AND truth table), one stale fallback in `stream.py` for a field that is now real on `Settings`, and one test-isolation hazard (`os.environ.setdefault` at module scope in `test_cors.py`).

No blocker-class defects were found.

## Warnings

### WR-01: `kloc_mcp_url` fallback in `_build_hydration_payload` is now dead code

**File:** `src/api/stream.py:422-424`
**Issue:** `mcp_url = getattr(settings, "kloc_mcp_url", "http://host.docker.internal:8765/mcp")` falls back to a literal "until dev-1's settings field ships in parallel" (per the inline comment). That field has shipped — `Settings.kloc_mcp_url` exists at `src/settings.py:78-89` with the same default. The `getattr` + default literal is unreachable and the comment is stale. Worse, if the field is ever renamed or accidentally removed, the silent literal fallback would mask the misconfiguration in production.
**Fix:**
```python
mcp_url = settings.kloc_mcp_url
```
Drop the stale "Fallback to that string literal until dev-1's settings field ships in parallel." comment block.

### WR-02: `Settings.llm_model_id` typed `str | None` but documented post-construction-invariant is `str`

**File:** `src/settings.py:66-74`, `src/api/stream.py:431,439`
**Issue:** The field declares `llm_model_id: str | None` and the docstring/comment at lines 62-65 promise "is always a non-empty `str` after construction". Static type checkers (mypy, pyright) see `str | None` and will flag `HydrationPayload(model_id=settings.llm_model_id, ...)` if `HydrationPayload.model_id` is typed as `str`. Callers must redundantly assert non-None. The annotation lies about the post-validation invariant.
**Fix:** Either tighten the annotation (preferred) or document why the union is necessary:
```python
llm_model_id: str = Field(
    default="",  # resolved by _validate_provider_key when empty
    ...
)
```
and switch the validator check from `is None` to `not self.llm_model_id`. Alternatively keep `str | None` but cast inside the validator and annotate the post-condition with `assert self.llm_model_id is not None`.

### WR-03: No regression test covering `DockerRunner` construction failure during lifespan

**File:** `src/main.py:102-111`, `tests/unit/`
**Issue:** The phase prompt explicitly calls out "does `src/main.py` fail loudly on `DockerRunner` construction errors". The code does propagate the error (no `try/except` wraps the construction), but there is no regression test that pins this contract. A future refactor that "helpfully" wraps the construction in `except Exception` to make tests easier would silently re-introduce the bug. The orphan-sweep below it (`except Exception as e:  # pragma: no cover - defensive`) demonstrates exactly the kind of swallow that needs guarding against.
**Fix:** Add a test under `tests/unit/test_lifespan_boot.py` (or similar) that monkey-patches `src.runner_mgmt.docker_runner.aiodocker` to `None` (or constructs `DockerRunner` directly) and asserts that entering the lifespan context raises the underlying `RuntimeError` — i.e., that the lifespan does not start the app cleanly when DockerRunner cannot be built.

### WR-04: HMAC-fallback 3-AND truth table is only partially covered

**File:** `tests/unit/test_settings.py:148-244`
**Issue:** The 3-AND predicate `allow_hmac_fallback AND secret==default AND not stub_mode` has 8 cells; the suite covers 4 (raises; allowed-by-stub; allowed-by-rotated-secret; allowed-by-allow_hmac_fallback=False). Untested combinations include:
- `allow_hmac_fallback=False, secret=default, stub_mode=True` (must allow)
- `allow_hmac_fallback=False, secret=rotated, stub_mode=False` (must allow — already implicitly covered by other tests but not explicitly)
- `allow_hmac_fallback=True, secret=rotated, stub_mode=True` (must allow — interaction of two escape hatches)

Without these, future drift that changes the operator (`and` → `or`) of the predicate would not be detected for some combinations.
**Fix:** Either parametrize a single `test_hmac_fallback_truth_table` with all 8 cells and the expected raise/allow outcome, or add the three missing positive-case tests.

### WR-05: No test for provider-key validator pass-through on `openrouter` / `bedrock`

**File:** `src/settings.py:159` (`# openrouter / bedrock: no key field on Settings yet — leave alone.`), `tests/unit/test_settings.py`
**Issue:** `LlmProvider = Literal["anthropic", "openrouter", "bedrock", "gemini"]`. The validator only enforces keys for `anthropic` and `gemini`. There is no test asserting that `LLM_PROVIDER=openrouter` or `LLM_PROVIDER=bedrock` constructs successfully without any key set, even with `stub_mode=False`. A future change that adds an `or self.llm_provider == "openrouter"` branch without a key field would silently start refusing valid boots.
**Fix:** Add `test_openrouter_provider_does_not_require_key` and `test_bedrock_provider_does_not_require_key` that assert `Settings(_env_file=None)` constructs cleanly with `LLM_PROVIDER=openrouter` / `bedrock` and `KLOC_STUB_MODE` unset.

### WR-06: Module-level `os.environ.setdefault` in `test_cors.py` leaks across the test session

**File:** `tests/unit/test_cors.py:25`
**Issue:** `os.environ.setdefault("KLOC_STUB_MODE", "true")` runs at import time and mutates the real process env. Pytest does not roll this back between test modules; any later test in the same `pytest` invocation that intentionally `monkeypatch.delenv("KLOC_STUB_MODE")` is fine, but any test that *reads* `os.environ["KLOC_STUB_MODE"]` without delete-first now sees the stub-mode flag set by an unrelated CORS test. Compare with `test_webhooks_hmac_fallback.py` which correctly uses `monkeypatch.setenv` everywhere. Beyond hygiene, this means `Settings()` defaults change depending on test-module order in CI — a flaky-test attractor.
**Fix:** Move the env mutation into a `@pytest.fixture(scope="module", autouse=True)` that uses `monkeypatch.setenv` (or the lower-level `MonkeyPatch.context()`), so cleanup happens after the module's last test:
```python
@pytest.fixture(scope="module", autouse=True)
def _stub_mode_for_module():
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    mp.setenv("KLOC_STUB_MODE", "true")
    yield
    mp.undo()
```
Importing `src.main` inside the fixture (or inside `client()`) instead of at module scope keeps the import-time read of `Settings` covered.

## Info

### IN-01: Comment-policy violations — issue/AC/reviewer IDs and history narration in `src/settings.py`

**File:** `src/settings.py:51,146-147,161-165`
**Issue:** CLAUDE.md comment policy: "never name people, plan sections, ACs, review rounds, or describe history". Violations:
- Line 51: `# Runner spawn config (dev-2 CR).` — names reviewer/contributor identity.
- Lines 146-147: `# Validate at boot, not first LLM call. Empty string was accepted silently before — now we require the key for the configured provider.` — narrates history of a previous bug.
- Line 161: `# ISS-07: opting into HMAC fallback while still using the placeholder bootstrap secret...` — leads with an issue tag.
**Fix:** Rewrite as standalone "why" comments without ID/history:
```python
# Refuse boot when the HMAC fallback is enabled and the bootstrap
# secret is still the well-known placeholder: any caller who learns
# the placeholder string could forge runner webhooks.
```

### IN-02: Comment-policy violations — plan sections, ACs, reviewer IDs, and history in `src/main.py`

**File:** `src/main.py:1-13,70,75,114,125,130-134`
**Issue:** Multiple violations of CLAUDE.md comment policy:
- Module docstring: `(Phase 1.A7 + 1.D / C1-10)`, `(per plan §700-704 + AC25)`, `(AC24 -> 'stream_orphaned')`, `dev-2 owns src/api/stream.py`.
- Line 75: `# B-INFRA-DISPATCH:` — reviewer-tag prefix.
- Line 114: `# 4. Boot-time orphan-container sweep (AC25).` — AC reference.
- Line 125 log string: `"boot: orphan_sweep not yet available (dev-2 Phase 2.D9) — skipping"` — names contributor + phase in log output.
- Lines 130-134: `# 5. ... (AC24). ... masking them was hiding QA's greenlet bug.` — AC reference + history narration naming "QA".
**Fix:** Strip phase/AC/reviewer/people references; rewrite remaining comments to explain only non-obvious *why*. Example for the orphan-message scan block:
```python
# Boot-time orphan-message scan: only catch transient DB errors
# (unreachable, network blip). Misconfig (missing greenlet, schema
# mismatch) must crash boot so it surfaces immediately.
```

### IN-03: Comment-policy violations — reviewer IDs, ISS tags, and history narration in `src/api/stream.py`

**File:** `src/api/stream.py:1,8,54,71-74,76,107-114,282-289,294-298,414-421,426`
**Issue:** Persistent comment-policy violations:
- Line 1: `dev-2-owned half of Track C`.
- Line 8: `(AC5, AC18)`.
- Lines 54, 76: `Contract A invariant #1`.
- Line 73-74: `(WR-03)`, Line 282: `(WR-02)`, Line 297: `WR-07.` — review-round IDs.
- Line 107: `# ISS-02: dedup persister spawn...` — issue ID lead.
- Line 414: `# Contract D pivot:` — plan section.
- Line 421: `# Fallback to that string literal until dev-1's settings field ships in parallel.` — names contributor + describes history (and the fallback itself is stale; see WR-01).
- Line 426: `# ISS-05` — issue ID.
**Fix:** Sweep these comments per the same rewrite pattern as IN-02. Several blocks (e.g., 71-74, 107-114, 282-298) can keep their *why* content if the reviewer-ID prefix is stripped.

### IN-04: Test-policy violation — `test_webhooks_hmac_fallback.py` patches a module attribute by direct assignment

**File:** `tests/unit/test_webhooks_hmac_fallback.py:101,114`
**Issue:** `webhooks.get_settings = _patched` mutates the module's attribute directly. The autouse `_reset_settings_cache` fixture restores it, but this hand-rolled save/restore is exactly what `monkeypatch.setattr(webhooks, "get_settings", _patched)` exists to do safely — including reverting on test failure paths the manual `finally` may miss if the fixture itself raises.
**Fix:** Replace the direct assignment with `monkeypatch.setattr(webhooks, "get_settings", _patched)` inside `_build_app` (take `monkeypatch` as a parameter) and drop the manual restore in `_reset_settings_cache`.

### IN-05: `kloc_runner_mode` removal not asserted in CI: `.env.example` test reads a real file but does not verify removal in production env

**File:** `tests/unit/test_settings.py:281-287`
**Issue:** `test_env_example_has_no_kloc_runner_mode_entry` only checks the committed `.env.example`. There is no equivalent check that `docker-compose.yml`, `runner/`, or any other tracked file no longer references `KLOC_RUNNER_MODE` — yet the field is gone from `Settings`. If a stale reference lingers in another file, an operator could still set it and silently expect stub mode without any error surfacing (Settings would drop it via `extra="ignore"` per `test_stale_kloc_runner_mode_env_var_is_ignored`, but the downstream consumer would still be broken).
**Fix:** Either widen the assertion to scan a small set of config files (`docker-compose.yml`, `Dockerfile`, `runner/Dockerfile`) for `KLOC_RUNNER_MODE`, or add a separate test:
```python
def test_no_tracked_file_references_kloc_runner_mode():
    import subprocess
    out = subprocess.check_output(
        ["git", "grep", "-l", "KLOC_RUNNER_MODE"], cwd=REPO_ROOT
    ).decode()
    # Only this test file (which mentions the symbol literally) is allowed.
    offenders = [l for l in out.splitlines() if not l.endswith("test_settings.py")]
    assert offenders == [], offenders
```

---

_Reviewed: 2026-05-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
