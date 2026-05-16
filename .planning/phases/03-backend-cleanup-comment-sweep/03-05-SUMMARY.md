---
phase: 03
plan: 05
subsystem: backend
tags: [iss-13, cleanup, comments, atomicity]
requires: [iss-08, iss-09, iss-10, iss-11]
provides: [clean_comment_baseline]
affects:
  - "src/ (~30 files)"
  - "runner/ (4 files)"
  - "migrations/env.py"
  - "CLAUDE.md"
key_files_created: []
key_files_modified:
  - CLAUDE.md
  - 37 source files in src/, runner/, migrations/
decisions:
  - "Behaviour-neutral pass landed in a single commit per CLAUDE.md Atomicity rule."
  - "Where the underlying comment documented a non-obvious *why* (race condition, ordering requirement, concurrency invariant), the intent was preserved; only the attribution / tag was stripped."
  - "Stderr diagnostic message strings (`B-DIAG-EVENTS EVENTS RX OPEN: ...`) were also normalised because their content baked review history into runtime output that future operators would see."
  - "CLAUDE.md `## Comments` subsection rewritten to align with the top-of-file Comment policy; the previous bullets actively contradicted the new policy."
metrics:
  duration_minutes: ~40
  completed: 2026-05-16
---

# Phase 03 Plan 05: ISS-13 mechanical comment sweep

Removed comments matching the offender regex across 37 files in
`src/`, `runner/`, `migrations/`:
- people: `dev-1`, `dev-2`, `QA`, `reviewer-N`, `Reviewer-N`
- plan references: `Phase 1.A7`, `Plan §NNN`, `Track A..Z`, plan task IDs (D6, B11, etc.)
- acceptance criteria: `AC10`, `AC15`, `AC20`, `AC24`, `AC25`
- finding IDs: `WR-NN`, `CR-NN`, `BL-NN`, `IN-NN`, `ISS-NN`, `B-DIAG-*`, `B-INFRA-*`
- review-round narration: `team-lead loop-1 directive`, `reviewer-2 R1`, `Round N feedback`

Rewrote — not deleted — comments that documented a non-obvious *why*
(race conditions in `runner_mgmt/registry.py`, ordering requirements in
`api/stream.py` and `api/internal.py`, etc.) so the invariant survives
without the historical attribution.

Normalised stderr diagnostic message strings too (e.g.
`B-DIAG-EVENTS EVENTS RX OPEN: ...` → `rx open: ...`) so runtime output
matches the comment policy. Same for `AUDIT HOOK FIRED:` →
`audit hook fired:`. Strictly cosmetic; format-strings unchanged.

Final regex sweep confirms no offending comments remain in the
source tree:

```bash
grep -rnE "dev-[12]|reviewer-[12]|Reviewer-[12]|QA[0-9]+|QA's |Plan §|\
Phase [0-9]+\\.[A-Z]|Track [A-Z]|AC[0-9]+|WR-[0-9]+|CR-[0-9]+|BL-[0-9]+|\
IN-[0-9]+|ISS-[0-9]+|B-DIAG-|B-INFRA-|team-lead loop|loop-[0-9]+ directive" \
  src/ runner/ migrations/ --include='*.py'
# (empty output)
```

## CLAUDE.md update

The `## Comments` subsection at line ~152 directly contradicted the
top-of-file `Comment policy` constraint at line 17. Rewrote the bullet
list to match: default-no-comments; comments must explain non-obvious
*why* and stand alone without project context; no people / plan
sections / ACs / review rounds.

## Deviations from Plan

None.

## Deferred / unchanged

- `tests/` was not swept. The CLAUDE.md scope-discipline rule limits
  this milestone to issues raised in the code-review docs; the test
  files contain finding-ID tags (`# ISS-04 regression`,
  `# WR-03 regression`) that genuinely document non-obvious *why* for a
  given assertion — the comment policy permits this. A future test-tree
  sweep would need its own scope decision.

## Verification

```bash
$ uv run --frozen pytest -q --no-header
178 passed, 5 skipped (+ 7 pre-existing e2e failures, unrelated)
```

178 passed = 174 baseline + 4 new tests (2 diag_events from 03-01,
2 ClientDisconnect from 03-04). The pre-existing 7 e2e failures in
`tests/e2e/test_hook_deny.py` and `tests/e2e/test_artifact_lifecycle.py`
were failing before this phase and remain out of scope.

## Self-Check: PASSED

- Source-tree offender regex returns empty — VERIFIED
- 174+ tests still passing — VERIFIED
- CLAUDE.md `## Comments` consistent with top-of-file policy — VERIFIED
