# Residual Code Review Issues

Findings still present after re-verifying the previous review docs
(`test-failures-root-cause-mapping.md`, `unmapped-findings.md`) against the
current branch. Most originally-listed findings have already been fixed in
code; what remains is enumerated here.

Verification basis: line-by-line read of `src/api/internal.py`,
`src/api/stream.py`, `src/api/webhooks.py`, `src/runner_mgmt/registry.py`,
`src/settings.py`, `runner/hooks/audit.py` as of the current HEAD.

---

## 1. `internal.py:117-125` — orphan-buffer flush publishes pre-RUN_STARTED frames BEFORE RUN_STARTED itself

**Severity**: High. Likely live contributor to Issue 2 (resume / cursor-replay
regression).

**Current code** (`src/api/internal.py:117-130`):

```python
if frame_type == "RUN_STARTED" and active_by_session is not None:
    active_by_session[session_id] = str(run_id)
    if pending_by_session is not None:
        pending = pending_by_session.pop(session_id, None)
        if pending and bus is not None:
            for buf in pending:
                await bus.publish(session_id, str(run_id), buf)  # flushed FIRST
# ... fall through ...
await bus.publish(session_id, str(run_id), frame)  # RUN_STARTED published SECOND
```

Subscribers see the event stream as
`[buffered frames..., RUN_STARTED, ...]` — RUN_STARTED is not the first
lifecycle frame for the run. AG-UI lifecycle ordering requires RUN_STARTED
first; downstream consumers (and the SSE generator's
`is_run_lifecycle_terminal`) may not be prepared to receive intermediate
frames before lifecycle has been observed.

**Fix**: publish the current RUN_STARTED frame first, THEN flush the buffer
in arrival order. Equivalently, move the flush block to after the publish
at line 162 (gated on `frame_type == "RUN_STARTED"`).

---

## 2. `stream.py:91-107` — comment claims reconnect-safe persister but code does not deduplicate

**Severity**: Medium. Narrow trigger (client re-POSTs `/sessions/{id}/stream`
for the same run), but the comment is misleading.

**Current code** (`src/api/stream.py:91-107`):

```python
persist_task = asyncio.create_task(_persist_events(...))
persist_task.add_done_callback(_log_persist_task_result)
# Track the task on app.state so reconnects don't double-spawn the
# persister and lifespan can drain it on shutdown.
pending = getattr(request.app.state, "persist_tasks", None)
if pending is None:
    pending = set()
    request.app.state.persist_tasks = pending
pending.add(persist_task)
persist_task.add_done_callback(pending.discard)
```

The task is unconditionally created. There's no check whether a persister
for `(session_id, run_id)` already exists in `pending`. If `stream_post`
runs twice for the same run, two persisters subscribe to the bus:

- both call `execution.append(wire)` → events doubled in the ring buffer
- each has its own `message_uuid: dict` → two assistant rows are inserted
  on the first delta (one of which may then race on `_next_seq` and retry)

**Fix**: key `pending` by `(session_id, run_id)`, return early if a
persister for that tuple is already running. Either a `dict` keyed by the
tuple, or check against existing tasks before `create_task`.

---

## 3. `settings.py:93-102` — `_validate_provider_key` body is a no-op

**Severity**: Low. Misconfiguration surfaces at first LLM call instead of
at boot.

**Current code** (`src/settings.py:93-102`):

```python
@model_validator(mode="after")
def _validate_provider_key(self) -> "Settings":
    # Validate at boot, not first LLM call. Empty string was accepted
    # silently before — now we require the key for the configured provider.
    if self.llm_provider == "anthropic" and not self.anthropic_api_key:
        # Allow missing in stub mode (tests / CI) — checked at runtime.
        pass
    if self.llm_provider == "gemini" and not self.gemini_api_key:
        pass
    return self
```

Both branches `pass`. The validator declares intent but does nothing. The
related default change (`str | None = None` at line 59-60) is in place and
correct.

**Fix**: either implement the boot-time check (raise if not in stub mode)
or remove the validator + the misleading comment.

---

## 4. `webhooks.py:259-294` — fallback HMAC secret still accepted in production

**Severity**: High (security). Originally listed against Issue 3 (warm-idle
eviction); still applies verbatim.

**Current code** (`src/api/webhooks.py:259-294`): `_resolve_runner_secret`
returns `(fallback_secret, "fallback")` whenever the registry has no entry
for `runner_id`. The source string is logged but not gated. After
warm-idle eviction + heartbeat-loss prune, an in-flight `BeforeToolCall`
hook authenticates via the bootstrap secret.

**Fix**: env-flag (default on outside dev) — e.g.
`KLOC_REJECT_FALLBACK_HMAC=1` — that returns 401 when `source == "fallback"`.
Audit log the rejection so dev-3's smoke greps can confirm enforcement.

---

## 5. `registry.py:148-187` — `get_or_spawn` race between check-and-spawn

**Severity**: Medium. Plausible contributor to Issue 3 (warm-idle eviction
+ respawn).

**Current code** (`src/runner_mgmt/registry.py:161-187`):

```python
entry = await self._get_entry(session_id)            # lock released
if entry is not None:
    await entry.warm_idle.await_kill_in_flight()     # no lock held
    entry = await self._get_entry(session_id)
    if entry is not None and await self._runner.is_alive(entry.handle):
        return entry
    if entry is not None:
        await self._remove_entry(session_id)
# No live container — spawn fresh.   ← two callers can both reach here
handle = await self._runner.spawn(hydration_payload)
new_entry = await self._install_entry(session_id, handle)
```

Two concurrent `get_or_spawn` calls for the same `session_id` after
eviction can both find `entry is None`, both reach `self._runner.spawn(...)`,
and the second `_install_entry` overwrites the first under `_lock` (line
259-263). One container is orphaned — eventually swept, but a window
exists where two runners exist for one session and either may receive the
next user message.

The previous reviewer flagged this as `partial`. The race is real; only
the failure mode (overwrite-and-orphan, not crash) was narrower than the
original claim.

**Fix**: per-session `asyncio.Lock` distinct from `self._lock`, held
across the check-then-spawn sequence. The registry-wide `_lock` must stay
short-lived to avoid the deadlock the existing comment warns about.

---

## 6. `internal.py:168-175` — RUN_FINISHED `active_by_session.pop` races a new RUN_STARTED

**Severity**: Low. Reconnect-during-run-handover trigger only.

**Current code** (`src/api/internal.py:168-175`):

```python
if (
    frame_type in ("RUN_FINISHED", "RUN_ERROR")
    and active_by_session is not None
):
    active_by_session.pop(session_id, None)
```

If a new run's RUN_STARTED arrives in a separate request between this
handler's `bus.publish` at line 162 and the pop at line 175, the pop wipes
out the *new* run's mapping. Subsequent intermediate frames for the new
run then take the orphan-buffer path.

`_dispatch_frame` is sequential within a single request, so this is a
cross-request race only — two concurrent `ingest_runner_events` calls
(runner reconnect handover, or back-to-back runs from a re-spawned runner).

**Fix**: compare-and-swap the pop:

```python
if active_by_session.get(session_id) == str(run_id):
    active_by_session.pop(session_id, None)
```

---

## 7. `runner/hooks/audit.py:_after_loop` — no drain on shutdown

**Severity**: Low. Audit completeness, not test flake.

**Current code** (`runner/hooks/audit.py:70-79`):

```python
async def stop(self) -> None:
    if self._after_worker:
        self._after_worker.cancel()
        try:
            await self._after_worker
        except (asyncio.CancelledError, BaseException):
            pass
    if self._http:
        await self._http.aclose()
        self._http = None
```

`stop()` cancels `_after_worker`. Pending `AfterToolCall` payloads in
`_after_queue` (up to 256) are discarded. Audit log loses post-mortem
events on graceful shutdown.

**Fix**: drain the queue before cancelling — pop and `_post` each pending
payload (bounded by `HOOK_DEADLINE_S * len(queue)`), then cancel. Or
expose a `flush()` method called from the runner's shutdown path.

---

## Mapping to the original 6 test failures

After this re-verification, the remaining live mappings are:

| Test failure | Residual contributor |
|---|---|
| Issue 1 — concurrent session cross-talk | None of the originally cited findings still apply. Needs fresh traceback. |
| Issue 2 — resume / cursor-replay regression | **#1** (orphan flush ordering) — primary suspect |
| Issue 3 — warm-idle eviction + respawn | **#4** (fallback HMAC) + **#5** (spawn race) |
| Issue 4 — vertical-slice RUN_FINISHED missing | None still apply. Needs fresh traceback. |
| Issue 5 — rehydrate / same_chat | Original mapping was speculative; no residual contributor identified. |
| Issue 6 — backend ClientDisconnect noise | Now handled gracefully at `internal.py:271`. Should be quiet. |

If Issues 1, 4, 5, 6 are still failing on a current branch run, the root
cause is not in the original review docs and is not in the residual list
above. Capture fresh tracebacks before continuing.

## Recommended fix order

1. **#1** (orphan flush ordering) — one-line move, addresses Issue 2.
2. **#4** (HMAC fallback gate) — small env-flag change, security-relevant.
3. **#5** (per-session spawn lock) — addresses Issue 3 race window.
4. **#2** (persister deduplication) — narrow but the comment is a lie.
5. **#6** (CAS pop on RUN_FINISHED) — small hardening.
6. **#3** (validator no-op) — either implement or delete the validator.
7. **#7** (audit drain on shutdown) — completeness, lowest urgency.
