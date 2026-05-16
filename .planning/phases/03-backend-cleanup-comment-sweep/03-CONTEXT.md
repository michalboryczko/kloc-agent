# Phase 3: Backend cleanup & comment sweep - Context

**Gathered:** 2026-05-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous) — recommended answers accepted in batch

<domain>
## Phase Boundary

Land low-risk hardening items (ISS-08..ISS-11) plus the 35-file mechanical
comment sweep (ISS-13) as cleanup work. Per CLAUDE.md "Atomicity": mechanical
sweep lands as a single behaviour-neutral commit. Per CLAUDE.md test policy:
cleanups (ISS-09, ISS-10, ISS-13) do not require new tests; bug-fix-flavored
cleanups (ISS-08, ISS-11) get a regression test if it would have caught the
issue.

In scope: ISS-08, ISS-09, ISS-10, ISS-11, ISS-13.

Out of scope: any change beyond what the issue specifies.

</domain>

<decisions>
## Implementation Decisions

### ISS-08 — Diagnostic gating
- Add `Settings.diag_events: bool = Field(default=False, ...)` in
  `src/settings.py`. Read it inside `_diag` in `src/api/internal.py` and
  `src/api/webhooks.py`. Phase 1 already gated the stream.py `_diag` via
  `KLOC_DIAG_EVENTS` env directly; align by routing through Settings so
  there is one source of truth (Phase 2 contract).
- Default off — verbose JSONL frame diagnostics are diagnostic-only.

### ISS-09 — app.state annotation cleanup
- Remove the unused annotated assignments at `src/main.py:83` and `:88`. They
  are noise — no contract.
- Do NOT introduce an `AppState` dataclass; that's a refactor, not a cleanup.

### ISS-10 — is_alive thundering-herd
- Add a small TTL result cache (~50 ms) on `RegistryEntry` so repeated
  `is_alive()` calls during a single hot loop don't fan out to the Docker API.
- Implementation lives in `src/runner_mgmt/registry.py` on `RegistryEntry`,
  not on the `Runner` protocol — keep the protocol clean.

### ISS-11 — ClientDisconnect response shape
- Return HTTP 499 ("client closed request", nginx convention) when no bytes
  were processed before disconnect. Return 204 when frames were processed
  before the disconnect. Distinction lives in `src/api/internal.py:_dispatch_frame`
  caller in the JSONL ingress (around lines 271-283).
- Log only — no new audit_log row or metric; that's out of scope.

### ISS-13 — Comment sweep
- Automated regex sweep across the 35 identified files, then one human-readable
  diff for sanity check before committing.
- Single behaviour-neutral commit per CLAUDE.md "Atomicity".
- Comment policy already lives in `CLAUDE.md` under "Comment policy". Confirm
  the wording matches the constraint at the top of CLAUDE.md ("default to no
  comments; comments must explain a non-obvious *why* and stand alone without
  project context; never name people, plan sections, ACs, review rounds, or
  describe history"). No new CONTRIBUTING.md.

### Claude's Discretion
- Whether to combine related issues into the same plan or keep one plan per
  issue — Claude decides based on file overlap and atomic-commit boundaries.
- Exact regex patterns for the comment sweep (the broad categories are
  enumerated in CLAUDE.md and `docs/reviews/code-review/issues.md`).
- Exact TTL value for `is_alive` cache (50 ms is the budget; tune within
  one order of magnitude as needed).

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_diag` helper already exists in `src/api/internal.py` and `src/api/webhooks.py`;
  Phase 1 added the env gate for stream.py. Pattern is established.
- `RegistryEntry` dataclass in `src/runner_mgmt/registry.py` is the right place
  for the is_alive cache.
- `ClientDisconnect` handling already exists in `src/api/internal.py` around
  the JSONL ingress loop — just needs to distinguish empty-body case.

### Established Patterns
- Settings field: `bool = Field(default=False, ...)` — follow ISS-08 alongside
  existing fields.
- Cache implementation: simple `(value, expires_at)` tuple, no dependency.
- HTTP error response: `JSONResponse(status_code=...)` already used in api/.

### Integration Points
- `src/settings.py` — add `diag_events` field.
- `src/api/internal.py` — gate `_diag`, return 499/204 distinction.
- `src/api/webhooks.py` — gate `_diag`.
- `src/main.py` — delete `:83` and `:88` annotated assignments.
- `src/runner_mgmt/registry.py` — TTL cache on `RegistryEntry.is_alive`.
- 35 files for comment sweep — listed in `docs/reviews/code-review/issues.md`
  (the planner agent should re-derive the list from current codebase since
  some Phase 1 / Phase 2 commits may have added/removed offending comments).

</code_context>

<specifics>
## Specific Ideas

- ISS-13 list count "~161 offending comments across 35 files" is from the
  Phase 0 baseline. Phase 1 fix-pass deliberately added inline finding-ID
  comments (`# WR-NN`, `# CR-NN`) that the sweep must remove. The planner
  should regenerate the sweep target list at plan time.
- Comment policy text in CLAUDE.md should match the wording at the top of the
  file under "Don't add error handling..." — review for consistency.

</specifics>

<deferred>
## Deferred Ideas

- Migrating `app.state.*` to a typed `AppState` dataclass — refactor, not a
  v1 cleanup concern.
- `audit_log` row for client disconnect events — observability extension, not
  a v1 hardening concern.
- Renaming `KLOC_DIAG_EVENTS` env var to match the new `diag_events` settings
  field — backwards-compatibility / naming concern, can wait.

</deferred>
