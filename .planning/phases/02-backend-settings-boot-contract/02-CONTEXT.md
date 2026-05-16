# Phase 2: Backend settings & boot contract - Context

**Gathered:** 2026-05-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous) — recommended answers accepted in batch

<domain>
## Phase Boundary

Make misconfiguration surface at boot, never inside the runner. Remove the
silent-degradation `stub` runner mode. Ensure HMAC fallback cannot use the
placeholder secret in production.

In scope: ISS-05 (provider config routes through Settings; boot validation),
ISS-07 (HMAC fallback validator), ISS-12 (remove `kloc_runner_mode=stub`).

Out of scope: changes to runner internals, AG-UI protocol, or audit code
beyond what's needed to delete `stub`-mode plumbing.

</domain>

<decisions>
## Implementation Decisions

### Settings validation strategy (ISS-05, ISS-07)
- Enforce config invariants via Pydantic `model_validator(mode="after")` —
  consistent with the existing `_validate_provider_key` pattern in
  `src/settings.py`.
- Validators raise `ValueError`; pydantic wraps to `ValidationError` at the
  call site, so consumers can catch either uniformly.
- `KLOC_STUB_MODE=true` remains the test/CI bypass; orthogonal from the
  now-removed `KLOC_RUNNER_MODE`.
- `os.environ.get("LLM_PROVIDER")` runtime override in `src/api/stream.py:432`
  is removed. Settings is the single source of truth; per-session model
  selection is a future concern, not Phase 2 scope.

### Stub-mode removal (ISS-12)
- `kloc_runner_mode` field is removed entirely from `Settings` and from
  `.env.example`. No deprecation period — locked in CLAUDE.md.
- Lifespan in `src/main.py` unconditionally constructs `DockerRunner`. If
  construction fails (ImportError, daemon unreachable), boot fails loudly —
  re-raise the original exception.
- Tests that previously set `KLOC_RUNNER_MODE=stub` migrate to injecting a
  fake `Runner` via `RunnerRegistry.set_runner()`. The registry's existing
  `set_runner` API is the fixture seam.
- `KLOC_STUB_MODE` env var stays — different concern (boot validation
  bypass for CI without provider keys), already used elsewhere.

### HMAC fallback hardening (ISS-07)
- The new validator lives in the same `Settings.model_validator(mode="after")`
  body as ISS-05.
- Default `kloc_hook_secret` value stays `"dev-secret-please-rotate"`. The
  combination `allow_hmac_fallback=True` AND default secret AND not stub_mode
  is what raises.
- `allow_hmac_fallback` keeps its `False` default; the new check only fires
  when the operator has explicitly opted in.
- Error message names the offending variables and the two ways to resolve
  ("set `KLOC_HOOK_SECRET` to a non-default value, or `KLOC_STUB_MODE=true`
  for test runs").

### Claude's Discretion
- Exact validator-message wording, field ordering inside the validator body,
  helper function vs inline checks — all at Claude's discretion.
- Test fixture name and helper module location for the `set_runner()` pattern.
- Whether to consolidate the two existing validators (`_validate_provider_key`
  + new ISS-05 + new ISS-07) into a single `_validate_all_invariants` or keep
  them split — Claude decides based on readability.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `src/settings.py:_validate_provider_key` — existing
  `model_validator(mode="after")` that raises `ValueError` unless
  `KLOC_STUB_MODE=true`. The ISS-05/ISS-07 validators follow this pattern.
- `src/runner_mgmt/registry.py:RunnerRegistry.set_runner()` — already exists
  at line 89; documented at line 69 as the "fail loudly until set_runner is
  called" seam. ISS-12 test migration uses this directly.
- `src/main.py:103-128` — current lifespan block that branches on
  `kloc_runner_mode`. Phase 2 removes the branch; keeps only the docker path.

### Established Patterns
- Settings: `BaseSettings` from `pydantic_settings`, validated at boot, cached
  via `lru_cache` singleton. All new validators follow this contract.
- Boot failure: `src/main.py` either re-raises or logs-and-skips depending on
  severity. ISS-12 says: fail loudly. ISS-05 and ISS-07 say: fail loudly.
- Tests: `tests/unit/test_settings.py` already exists (added in Phase 1
  init-state baseline). Phase 2 extends it with ISS-05 and ISS-07 cases.

### Integration Points
- `src/api/stream.py:429-447` — the spot where `os.environ.get("LLM_PROVIDER")`
  and `LLM_MODEL_ID` reads happen. Replace with `settings.llm_provider` /
  `settings.llm_model_id`.
- `src/main.py:103-128` — lifespan startup; collapse to single docker path.
- `src/settings.py` — add two new validators in the existing `model_validator`
  family.
- `tests/unit/test_settings.py` — extend.
- `tests/unit/test_webhooks_hmac_fallback.py` — extend or replace ISS-07
  coverage.
- `.env.example` — remove `KLOC_RUNNER_MODE` entry.

</code_context>

<specifics>
## Specific Ideas

- ISS-05 success criterion explicitly names lines 347-353 in
  `src/api/stream.py` — those are the `os.environ.get` reads to remove.
  Current code now has them at lines 432-438 (after Phase 1 changes); planner
  must re-locate.
- ISS-07 success criterion says: validator raises when `allow_hmac_fallback`
  AND default secret AND NOT stub_mode — exact 3-AND predicate.
- ISS-12 success criterion enumerates: remove `kloc_runner_mode` from
  `Settings`, remove from `.env.example`, lifespan unconditional, fail loudly,
  tests use `set_runner()`.

</specifics>

<deferred>
## Deferred Ideas

- Per-session provider/model selection at runtime — out of scope; would need
  a new Settings field or request-level override field. Not on the roadmap.
- Consolidating Settings validators into a domain-driven layout — refactor,
  not a v1 hardening concern.
- Removing `KLOC_STUB_MODE` entirely — would require redesigning how CI
  starts the backend without real provider keys. Out of scope.

</deferred>
