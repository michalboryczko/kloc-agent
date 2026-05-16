---
phase: 03-backend-cleanup-comment-sweep
fixed_at: 2026-05-16T17:12:00Z
review_path: .planning/phases/03-backend-cleanup-comment-sweep/03-REVIEW.md
iteration: 1
findings_in_scope: 2
fixed: 2
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-05-16T17:12:00Z
**Source review:** .planning/phases/03-backend-cleanup-comment-sweep/03-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 2 (both Warnings; 4 Info findings out of scope per fix policy)
- Fixed: 2
- Skipped: 0

## Fixed Issues

### WR-01: `_diag` always calls `get_settings()` even when disabled — makes test_internal.py implicitly env-dependent

**Files modified:** `src/api/internal.py`, `src/api/webhooks.py`
**Commit:** ba5afae86
**Applied fix:** Introduced a module-level `_diag_enabled()` helper in both
`internal.py` and `webhooks.py`, decorated with `functools.lru_cache(maxsize=1)`.
The helper wraps `get_settings().diag_events` in a `try/except Exception:` that
returns `False` on any construction failure (fail-safe, since diagnostics are
best-effort). `_diag(msg)` now gates on `_diag_enabled()` instead of calling
`get_settings()` on every invocation. Net effect:
- Disabled path no longer constructs `Settings`, so missing
  `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` no longer turns `_diag` into a
  `ValidationError`.
- `tests/unit/test_internal.py` no longer needs env coupling or
  `get_settings.cache_clear()` ordering tricks — verified locally with
  unit + integration suites (170 passed, 5 skipped).
- When diag is enabled, the per-call cost drops from a dict lookup +
  attribute access to a single cached boolean.

The fail-safe `try/except` (option (b) from the review) was layered onto
option (a) so that a future change to `Settings` validation can never turn
a stderr diagnostic call into a fatal error.

### WR-02: Comment sweep missed plan-section reference in `src/main.py`

**Files modified:** `src/main.py`
**Commit:** dea996ec7
**Applied fix:** Rewrote the `# 2. S3 client (lifespan-managed; research/04 §6.4)`
comment on `src/main.py:44` to drop the planning-artefact cross-reference and
preserve the *why* (lifespan-scoping so credentials and connection are created
once at boot, not per request). Matches the ISS-13 comment policy from
`CLAUDE.md` ("comments must explain a non-obvious *why* and stand alone
without project context; never name people, plan sections, ACs, …").

Out-of-scope sister references that the reviewer flagged for follow-up
(`runner/Dockerfile:33`, `.env.example:3`) were left untouched per WR-02's
scope, as were three additional in-`src/` matches the reviewer's grep
appears to have missed (`src/storage/s3.py:41`, `src/db/models.py:36`,
`src/api/sessions.py:30`). These can be addressed in a follow-up sweep
if the orchestrator chooses; they are not part of WR-02's stated scope.

---

_Fixed: 2026-05-16T17:12:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
