---
phase: 03-backend-cleanup-comment-sweep
verified: 2026-05-16T18:00:00Z
status: passed
score: 5/5 must-haves verified (gap closed in commit 70a28c235)
overrides_applied: 0
gaps_closed:
  - truth: "Comment sweep regex from ROADMAP SC#5 returns zero matches in src/ runner/"
    status: closed_in_70a28c235
    reason: "ROADMAP success criterion #5 specifies the regex `grep -rE 'dev-[0-9]|reviewer-[0-9]|plan §|B-DIAG|B-INFRA|AC[0-9]+|Phase [0-9]' src/ runner/ --include='*.py'` must return zero matches. It returns one match in `runner/__main__.py:209` (`# H4 — intentionally deferred per dev-3.`), which is a named-person attribution plus a plan-task-ID and is precisely the offender class the sweep was meant to remove. The SUMMARY claimed empty output, but the SUMMARY ran a different regex variant that excluded the bare `dev-[0-9]` pattern."
    artifacts:
      - path: "runner/__main__.py"
        issue: "Line 209 retains `# H4 — intentionally deferred per dev-3.` — names a developer (dev-3) and tags a plan task ID (H4); lines 160 and 218 additionally reference `Architecture.md §3.4` and `investigation.md §8` which violate the policy's plan-section clause (though those are not matched by the SC#5 regex)."
    missing:
      - "Rewrite `runner/__main__.py:209` to drop the `H4`/`dev-3` tag and preserve only the non-obvious *why* (OTel SDK already pre-installed by `opentelemetry-instrument` ENTRYPOINT; explicit `setup_console_exporter()` would duplicate or no-op)."
      - "Consider also rewriting lines 160 and 218 to drop `Architecture.md §3.4` and `investigation.md §8` plan-document references per the CLAUDE.md Comments policy (`comments must stand alone without project context`); not blocking SC#5 but inconsistent with the policy."
      - "Re-run the exact SC#5 grep to confirm zero matches before claiming sweep complete."
---

# Phase 3: Backend cleanup & comment sweep — Verification Report

**Phase Goal:** Land the low-risk hardening items plus the 35-file mechanical comment sweep as a single behaviour-neutral commit. Codify the comment policy so the rot does not return.
**Verified:** 2026-05-16T18:00:00Z
**Status:** gaps_found
**Re-verification:** No — initial verification.

## Goal Achievement

### Observable Truths (from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `_diag` calls in `src/api/internal.py` and `src/api/webhooks.py` no longer write to stderr by default; gated by `Settings.diag_events` | VERIFIED | `src/settings.py:132` declares `diag_events: bool = Field(...)`; `src/api/internal.py:37-43` and `src/api/webhooks.py:48-54` define `@lru_cache _diag_enabled()` reading `get_settings().diag_events`; both `_diag(msg)` helpers return early when disabled. Default `False` confirmed by `tests/unit/test_settings.py::test_diag_events_defaults_to_false`. |
| 2 | `app.state.active_run_by_session` and `app.state.pending_pre_run_started` no longer use PEP-526 discarded annotated assignments | VERIFIED | `src/main.py:75,80` show plain assignments `app.state.active_run_by_session = {}` and `app.state.pending_pre_run_started = {}` — annotations removed. |
| 3 | `is_alive` thundering-herd addressed (~50 ms result cache on the entry) | VERIFIED | `src/runner_mgmt/registry.py:37` defines `_IS_ALIVE_TTL_S = 0.05`; `RegistryEntry._is_alive_cache: tuple[bool, float] \| None` field at line 52; `RegistryEntry.is_alive(runner)` at lines 56-69 implements TTL-cached wrap around `runner.is_alive(self.handle)` using `time.monotonic()`. Two call sites in `RunnerRegistry.get_or_spawn` (lines 227, 243) now invoke `entry.is_alive(self._runner)`. |
| 4 | `ClientDisconnect` response shape distinguishes "no bytes received" from "some frames received then disconnect" | VERIFIED | `src/api/internal.py:267-279` — `except ClientDisconnect:` branch returns `Response(status_code=499)` when `count == 0`, otherwise `Response(status_code=status.HTTP_204_NO_CONTENT)`. Two regression tests added: `tests/unit/test_internal.py::test_client_disconnect_returns_499_when_no_frames` (line 192) and `test_client_disconnect_returns_204_when_some_frames` (line 205). |
| 5 | Comment sweep across the 35 files lands as one mechanical PR removing offending comments; `grep -rE 'dev-[0-9]\|reviewer-[0-9]\|plan §\|B-DIAG\|B-INFRA\|AC[0-9]+\|Phase [0-9]' src/ runner/ --include='*.py'` returns zero matches; comment policy added to `CLAUDE.md` | **FAILED** | The exact SC#5 regex returns **one** match: `runner/__main__.py:    # H4 — intentionally deferred per dev-3. opentelemetry-instrument`. CLAUDE.md `## Comments` section (lines 152+) IS rewritten to match the top-of-file policy (default-no-comments; no people / plan sections / ACs / review rounds). Other offender classes (`# WR-NN`, `# CR-NN`, `# AC[0-9]+`, `# (dev\|QA\|Reviewer)-[0-9]` beyond this one) return empty. The sweep is 99% complete but misses the singular offender. |

**Score:** 4/5 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/settings.py` | `diag_events` Settings field | VERIFIED | Field defined line 132 with `validation_alias="KLOC_DIAG_EVENTS"`, default `False`. |
| `src/api/internal.py` | `_diag` gated through Settings; `ClientDisconnect` 499/204 | VERIFIED | `_diag_enabled()` `@lru_cache(maxsize=1)` wrapped (per WR-01 fix); 499/204 branches present at lines 277-279. |
| `src/api/webhooks.py` | `_diag` gated through Settings | VERIFIED | `_diag_enabled()` mirrored from internal.py at lines 48-54. |
| `src/main.py` | Annotated assignments removed; no plan-section refs | VERIFIED | Plain assignments at lines 75, 80; WR-02 fix landed at line 44 ("S3 client (lifespan-managed so credentials/connection are created once at boot, not per-request)" — no `research/04 §6.4` reference). |
| `src/runner_mgmt/registry.py` | TTL-cached `is_alive` on `RegistryEntry` | VERIFIED | `_IS_ALIVE_TTL_S = 0.05`, `_is_alive_cache` field, `is_alive(runner)` method, both call sites updated. |
| `runner/__main__.py` | Free of named-person / finding-ID comments | **STUB / INCOMPLETE** | Line 209 retains `# H4 — intentionally deferred per dev-3.` — direct SC#5 regex failure. |
| `CLAUDE.md` | `## Comments` section codifies policy | VERIFIED | Section rewritten to "Default to no comments... must stand alone without project context. Do not reference people, plan sections, acceptance criteria, review rounds, finding IDs..." matches top-of-file policy. |
| `tests/unit/test_settings.py` | Regression for `diag_events` default | VERIFIED | `test_diag_events_defaults_to_false` (line 380), `test_diag_events_can_be_enabled_via_env` (line 393). |
| `tests/unit/test_internal.py` | Regression for 499/204 | VERIFIED | Both new tests present (lines 192, 205). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `_diag` (internal.py) | `Settings.diag_events` | `get_settings().diag_events` inside `_diag_enabled()` | WIRED | Verified by grep at internal.py:38-43; `_diag` (line 47) gates on `_diag_enabled()`. |
| `_diag` (webhooks.py) | `Settings.diag_events` | `get_settings().diag_events` inside `_diag_enabled()` | WIRED | Same pattern at webhooks.py:48-54. |
| `RunnerRegistry.get_or_spawn` | `RegistryEntry.is_alive` | direct method call | WIRED | Call sites at registry.py:227 and registry.py:243 confirmed. |
| `JSONL ingress` | 499/204 distinction | `count == 0` branch in `except ClientDisconnect:` | WIRED | internal.py:267-279. |
| CLAUDE.md `## Comments` | top-of-file Comment policy constraint | re-stated in the section | WIRED | Section rewritten; bullets match the constraint at the top of CLAUDE.md. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Settings field `diag_events` declared with correct default | `grep -n "diag_events" src/settings.py` | One match at line 132 | PASS |
| `_diag_enabled()` cached and `@lru_cache`-decorated in both files | `grep -n "_diag_enabled\|lru_cache" src/api/internal.py src/api/webhooks.py` | Both files have `@functools.lru_cache(maxsize=1)` + `def _diag_enabled()` | PASS |
| 499/204 distinction present | `grep -n "499\|HTTP_204_NO_CONTENT" src/api/internal.py` | 499 at line 278, 204 at lines 279, 309, 315 | PASS |
| Annotated assignments removed in main.py | `grep -n "app.state.active_run_by_session\|app.state.pending_pre_run_started" src/main.py` | Plain assignments at lines 75, 80; no `: dict[str, str]` annotation | PASS |
| `RegistryEntry.is_alive` TTL cache exists | `grep -n "_IS_ALIVE_TTL_S\|_is_alive_cache" src/runner_mgmt/registry.py` | Constant at line 37, field at 52, method at 56 | PASS |
| ROADMAP SC#5 regex returns zero matches | `grep -rE 'dev-[0-9]\|reviewer-[0-9]\|plan §\|B-DIAG\|B-INFRA\|AC[0-9]+\|Phase [0-9]' src/ runner/ --include='*.py'` | `runner/__main__.py:    # H4 — intentionally deferred per dev-3.` | **FAIL** |
| Full test suite passes (excluding 7 pre-existing e2e failures) | `uv run --frozen pytest -q --no-header` | `7 failed, 178 passed, 5 skipped, 12 deselected in 7.21s` — 7 failures are in `tests/e2e/test_artifact_lifecycle.py` and `tests/e2e/test_hook_deny.py` (pre-existing per SUMMARY) | PASS (178/178 in-scope) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| ISS-08 | 03-01-PLAN | `_diag` writes gated behind `Settings.diag_events`; default off | SATISFIED | Truth #1 above. |
| ISS-09 | 03-02-PLAN | `app.state.*` annotated assignments removed | SATISFIED | Truth #2 above. |
| ISS-10 | 03-03-PLAN | `is_alive` thundering-herd ~50 ms cache | SATISFIED | Truth #3 above. |
| ISS-11 | 03-04-PLAN | `ClientDisconnect` distinguishes no-bytes from frames-then-drop | SATISFIED | Truth #4 above. |
| ISS-13 | 03-05-PLAN | Mechanical comment sweep across 35 files; comment policy in `CLAUDE.md` | **PARTIALLY SATISFIED** | Policy section rewritten correctly; sweep covers ~36 files; one residual offender remains in `runner/__main__.py:209` (`dev-3` + `H4`). |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `runner/__main__.py` | 209 | `# H4 — intentionally deferred per dev-3.` — named-person attribution + plan-task ID | BLOCKER | Direct violation of ROADMAP SC#5 (`grep -rE 'dev-[0-9]\|...' src/ runner/` must return zero matches). |
| `runner/__main__.py` | 160 | `# Architecture.md §3.4: ag_ui_strands.StrandsAgent rebuilds...` — plan-document reference | WARNING | Violates the CLAUDE.md Comments policy ("must stand alone without project context") but does not match the SC#5 regex. |
| `runner/__main__.py` | 218 | `# investigation.md §8).` — plan-document reference | WARNING | Same as above. |
| `migrations/versions/2026_05_14_0001_init.py` | 7-9 | Module docstring references `research/04 §4.5` and `§1.2-1.5 in research/04` | INFO (out of scope) | Plan-05 task1 file list scoped the sweep to `migrations/env.py`, not the `versions/` subdir; this file was deliberately omitted. Not a phase failure, but candidate for follow-up sweep. |

### Gaps Summary

The four substantive hardening items (ISS-08, ISS-09, ISS-10, ISS-11) all land cleanly and as specified. Code Review WR-01 (eager `get_settings()` in `_diag`) and WR-02 (missed `research/04 §6.4` in `src/main.py:44`) were both addressed in 03-REVIEW-FIX.md and the fixes are present in the codebase.

The single gap is in ISS-13. The mechanical sweep is overwhelmingly complete — 36 of 37 in-scope files appear clean against the offender regex set — but `runner/__main__.py:209` retains the comment `# H4 — intentionally deferred per dev-3.`. This comment alone fails the ROADMAP SC#5 acceptance check (`grep -rE 'dev-[0-9]|...' src/ runner/ --include='*.py'` must return zero matches; it currently returns one match on this line).

The SUMMARY for 03-05 reported the offender regex as empty, but the regex it ran was a hand-built variant that did not include the bare `dev-[0-9]` alternation pattern from the ROADMAP. The ROADMAP's grep is the contract; SUMMARY's regex is not.

Two adjacent lines in the same file (`runner/__main__.py:160` and `:218`) reference `Architecture.md §3.4` and `investigation.md §8` respectively. These violate the CLAUDE.md `## Comments` policy ("must stand alone without project context") but are *not* in the SC#5 regex, so they are flagged as WARNINGs only — fix opportunistically when patching line 209.

**Recommended remediation (single small commit):**
1. Rewrite `runner/__main__.py:209-218` to drop the `H4`/`dev-3`/`investigation.md §8` references, retain the *why* (OTel SDK already pre-installed by the `opentelemetry-instrument` ENTRYPOINT; explicit `StrandsTelemetry().setup_console_exporter()` would duplicate or no-op).
2. Optionally rewrite line 160 to drop the `Architecture.md §3.4` reference while preserving the StrandsAgent message-list rebuild *why*.
3. Re-run `grep -rE 'dev-[0-9]|reviewer-[0-9]|plan §|B-DIAG|B-INFRA|AC[0-9]+|Phase [0-9]' src/ runner/ --include='*.py'` and confirm empty.

This is a one-file, ~10-line follow-up; SC#1-#4 are unaffected.

---

_Verified: 2026-05-16T18:00:00Z_
_Verifier: Claude (gsd-verifier)_
