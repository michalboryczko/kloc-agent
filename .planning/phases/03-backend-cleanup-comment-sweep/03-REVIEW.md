---
phase: 03-backend-cleanup-comment-sweep
reviewed: 2026-05-16T17:05:38Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - src/settings.py
  - src/main.py
  - src/api/internal.py
  - src/api/webhooks.py
  - src/api/stream.py
  - src/runner_mgmt/registry.py
  - tests/unit/test_internal.py
  - tests/unit/test_settings.py
findings:
  critical: 0
  warning: 2
  info: 4
  total: 6
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-05-16T17:05:38Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Phase 3 lands four substantive changes plus a behaviour-neutral comment
sweep:

- **ISS-08** — `Settings.diag_events` field added; both `_diag` helpers in
  `internal.py` and `webhooks.py` route through it. `stream.py` had no
  `_diag` helper to align (correctly documented as a no-op in the plan).
- **ISS-10** — 50 ms TTL cache around `RegistryEntry.is_alive`. Lives on
  the entry (per-entry, no shared state), so invalidation follows entry
  lifetime.
- **ISS-11** — JSONL ingress `ClientDisconnect` now distinguishes empty
  body (499) from partial progress (≥ 1 frame dispatched → 204). The
  distinction is keyed on `count`, not `total_bytes` — partial bytes
  with no complete frame map to 499, which is the correct interpretation
  ("we accepted N AG-UI frames").
- **ISS-13** — comment sweep: finding-IDs, plan-section references, AC
  numbers, named-person attributions, and review-round narration removed.
  Where the underlying *why* was non-obvious (race conditions, ordering
  contracts, concurrency invariants), the substance was preserved.

The mechanical changes look sound on direct read-through. Bugs flagged
below cluster around two themes: (a) the new `_diag` gating eagerly calls
`get_settings()` even when diagnostics are off, which makes
`tests/unit/test_internal.py` implicitly depend on developer-shell env
vars, and (b) the comment sweep missed one plan-section reference in
`src/main.py`.

## Warnings

### WR-01: `_diag` always calls `get_settings()` even when disabled — makes test_internal.py implicitly env-dependent

**Files:**
- `src/api/internal.py:36-41`
- `src/api/webhooks.py:47-52`
- `tests/unit/test_internal.py` (whole file — no env setup)

**Issue:**
Both new `_diag` helpers call `get_settings()` on **every** invocation,
including the disabled path:

```python
def _diag(msg: str) -> None:
    if not get_settings().diag_events:
        return
    print(msg, file=sys.stderr, flush=True)
```

`get_settings()` is `lru_cache`d, so the cost after first call is a dict
lookup. The functional issue is the *first* call: Settings construction
runs `_validate_provider_key`, which raises `ValueError` when
`LLM_PROVIDER=gemini` (the default) and `GEMINI_API_KEY` is unset and
`KLOC_STUB_MODE` is unset.

`tests/unit/test_internal.py` calls `_dispatch_frame(...)` and
`ingest_runner_events(...)` without any `monkeypatch.setenv("KLOC_STUB_MODE", "true")`
and without `get_settings.cache_clear()`. The tests *appear* green only
because (a) the developer's shell exports `GEMINI_API_KEY` /
`KLOC_STUB_MODE`, or (b) an earlier unit test (e.g. `test_settings.py`)
warmed the `lru_cache` with a Settings instance built under monkeypatched
env. Reproduced locally:

```
$ env -i HOME=$HOME PATH=$PATH .venv/bin/python -c "
from src.settings import get_settings; get_settings()
"
FAIL: ValidationError 1 validation error for Settings
  Value error, GEMINI_API_KEY required when llm_provider=gemini
```

Pre-ISS-08, `_diag` in `internal.py` gated on the module-level constant
`_DIAG_ENABLED = bool(os.environ.get("KLOC_DIAG_EVENTS"))` — no Settings
construction, no env coupling. The new code regresses test isolation.

**Fix:** either (a) cache the boolean once at module import, e.g.

```python
import functools

@functools.lru_cache(maxsize=1)
def _diag_enabled() -> bool:
    return get_settings().diag_events

def _diag(msg: str) -> None:
    if not _diag_enabled():
        return
    print(msg, file=sys.stderr, flush=True)
```

…or (b) swallow Settings construction failures inside `_diag` (cheap and
fail-safe — diagnostics are best-effort by definition):

```python
def _diag(msg: str) -> None:
    try:
        if not get_settings().diag_events:
            return
    except Exception:
        return
    print(msg, file=sys.stderr, flush=True)
```

…or (c) add an autouse fixture to `tests/conftest.py` that sets
`KLOC_STUB_MODE=true` + `GEMINI_API_KEY=stub` for the unit suite and
clears `get_settings.cache_clear()` between tests, so test_internal.py
no longer leans on previous-test ordering.

---

### WR-02: Comment sweep missed plan-section reference in `src/main.py`

**File:** `src/main.py:44`

**Issue:**
The ISS-13 comment policy (per CLAUDE.md "Comments" subsection and the
phase-03-05 commit message) forbids "plan-section references" such as
`research/04 §6.4`. The sweep removed every other such reference under
`src/` (verified: `grep -rn "§\|research/" src/` returns only this
line), but this one survived:

```python
    # 2. S3 client (lifespan-managed; research/04 §6.4)
```

Comparable reference also present out-of-scope in `runner/Dockerfile:33`
(`B-INFRA-4:`), and `.env.example:3` (`Owner: dev-1 (backend). dev-2 / dev-3 …`).
Flagging only the in-scope one here; the others should be picked up if a
follow-up sweep runs.

**Fix:** rewrite the comment to either drop the cross-reference or
restate the *why* without the planning artefact:

```python
    # 2. S3 client (lifespan-managed so credentials/connection are
    #    created once at boot, not per-request).
```

## Info

### IN-01: `_diag` gating allocates a formatted string on every call even when disabled

**Files:**
- `src/api/internal.py` — multiple `_diag(f"...")` call sites (e.g. lines 60, 198, 209, 262, 270)
- `src/api/webhooks.py` — call sites at 97, 114, 124, 131, 140

**Issue:**
`_diag` accepts a pre-built string, so callers always pay the
`f"..."` interpolation cost (and the f-string's `repr()` calls for
e.g. `{(authorization or '')[:24]!r}` in `webhooks.py:100`) even when
`diag_events=False`. On the JSONL ingress hot path this is one
interpolation per AG-UI frame plus one per chunk — pre-formatted but
discarded.

The pre-change code had the same shape, so this is not a regression;
it just doesn't realise the "off by default" goal in full. Pure quality
note.

**Fix:** if the formatting cost ever matters, take a callable:

```python
def _diag(build: Callable[[], str]) -> None:
    if not get_settings().diag_events:
        return
    print(build(), file=sys.stderr, flush=True)

_diag(lambda: f"frame: type={frame_type} run_id={run_id} ...")
```

Probably not worth doing for v1.

---

### IN-02: `_is_alive_cache` TTL caches both True and False — a transient `False` (e.g. Docker daemon hiccup) triggers an immediate respawn

**File:** `src/runner_mgmt/registry.py:56-69`

**Issue:**
The cache stores whatever `runner.is_alive(self.handle)` returned, True
or False. A transient False (e.g. aiodocker raises during a daemon
reload, the implementation maps it to "not alive") is locked in for
50 ms. Within `get_or_spawn`, that False triggers `_remove_entry` +
spawn. A real container that was actually alive gets torn down by the
warm-idle / shutdown path on the next pass.

In practice the window is tiny (50 ms) and the `Runner` Protocol impl
in `docker_runner.py` is robust enough that transient False is rare.
Surfacing only as an INFO so the trade-off is on record.

**Fix:** consider caching only True (positive results) and always doing
a fresh check on False, since the cost of a duplicate respawn is much
higher than a duplicate `is_alive` round-trip. Not required for v1.

---

### IN-03: `is_alive` cache mutation is not lock-protected — concurrent first-callers do redundant Docker round-trips

**File:** `src/runner_mgmt/registry.py:64-68`

**Issue:**
Read-modify-write on `self._is_alive_cache` is not guarded. Under a
single asyncio event loop the dataclass attribute write is atomic, so
this is not a data-race in the C-level sense — but two concurrent
callers entering before the cache is populated each do an independent
`await runner.is_alive(self.handle)`. Both write similar values at
completion; the later write wins. No correctness bug; just one extra
Docker round-trip in the cold-cache contention window.

The cache *does* deliver the documented benefit on the hot reuse loop
(registry revalidation followed by spawn-lock double-check in
`get_or_spawn`), because by the second call the first has populated
the cache. So the stated motivation holds; just noting that the cold-
cache concurrent case doesn't collapse.

**Fix:** none required. If desired, replace the tuple with an
`asyncio.Event`-gated single-flight:

```python
async def is_alive(self, runner) -> bool:
    ...  # If a check is in flight, await the existing future
```

Likely not worth the complexity for a 50 ms window.

---

### IN-04: `_diag` writes in webhooks.py log a 24-byte prefix of the Authorization header

**File:** `src/api/webhooks.py:97-101`

**Issue:**
When `diag_events=True`, the receiver logs:

```python
_diag(
    f"auth rx: runner_id={runner_id} ts_hdr={x_kloc_hook_ts} "
    f"body_len={len(raw_body)} "
    f"sig_hdr_prefix={(authorization or '')[:24]!r}"
)
```

The first 24 chars of `Authorization: HMAC <b64sig>` exposes ~16 chars
of base64-encoded HMAC-SHA256 signature. That's 12 bytes of the 32-byte
MAC — still 96 bits of effective remaining entropy, so not a
catastrophic leak, but it does provide an offline attacker who captures
a stderr dump with a known signing input some material to validate guesses
against. Pre-existing in the file; comment sweep didn't introduce or
remove it. Flagging because the diag-gating change is the right moment
to think about whether stderr diagnostics should ever expose signature
prefixes at all.

**Fix:** if this is intentional for operator smoke checks, document it
(short comment near `_diag`); otherwise truncate to fewer bytes (8?) or
just log `sig_hdr_present=True/False`.

---

_Reviewed: 2026-05-16T17:05:38Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
