# Code Review: kloc-agent (post fix-sprint baseline)

**Reviewer**: claude (Opus 4.7)
**Date**: 2026-05-16
**Branch**: `master` (uncommitted working tree on top of `13fd93f57 WIP: kloc-agent-poc - baseline before fix sprint`)
**Scope**: every changed non-`node_modules` / non-`__pycache__` file in `git diff HEAD`
**Verdict**: **REQUEST_CHANGES**

---

## Summary

The fix sprint has landed real fixes for four of the seven items in `docs/reviews/residual-issues.md` (HMAC fallback, spawn race, settings validator, fail-closed audit on unknown errors) and added solid reconnect/dedup hardening (runner channel reconnect loop, event_bus sentinel-on-full, message-seq UNIQUE retry, `_remove_entry` `expected_runner_id` guard).

However, three items from the residual list are **not** addressed in the working tree — including the orphan-buffer ordering bug that the previous reviewer marked as the primary suspect for the resume/cursor-replay regression — and one new inconsistency was introduced in `_build_hydration_payload`. None of these are large changes; all should land before merge.

---

## Findings

### Critical (must fix)

#### C1. `src/api/internal.py:117-130` — pre-RUN_STARTED buffer still flushed BEFORE the RUN_STARTED frame

The `_dispatch_frame` flow on a `RUN_STARTED` arrival is:

```python
if frame_type == "RUN_STARTED" and active_by_session is not None:
    active_by_session[session_id] = str(run_id)
    if pending_by_session is not None:
        pending = pending_by_session.pop(session_id, None)
        if pending and bus is not None:
            for buf in pending:
                await bus.publish(session_id, str(run_id), buf)   # buffered frames go first
# ...fall through to line 162:
await bus.publish(session_id, str(run_id), frame)                 # RUN_STARTED published second
```

The buffered frames are published before the RUN_STARTED frame itself, so subscribers see `[buffered..., RUN_STARTED, ...]`. AG-UI lifecycle ordering requires RUN_STARTED first; `is_run_lifecycle_terminal` and the SSE generator can short-circuit on intermediate frames that arrive before lifecycle, and the persister's `_ensure_assistant_row` may run before the run's bus topic is fully wired downstream.

This is the residual-issues.md `#1` finding verbatim, marked by the prior reviewer as the primary suspect for Issue 2 (resume / cursor-replay regression). It is the simplest fix on the list and should land first.

- **Fix**: publish the current `RUN_STARTED` first, then flush the pending buffer. Move the flush block to after the publish at line 162, gated on `frame_type == "RUN_STARTED"`. Confirm with a unit test that simulates: orphan frame → RUN_STARTED → terminal lifecycle, and asserts subscriber observes RUN_STARTED in position 0.

---

### High (should fix)

#### H1. `src/api/stream.py:98-114` — persister task is unconditionally created; comment misrepresents behaviour

The comment claims the `request.app.state.persist_tasks` set "tracks the task on app.state so reconnects don't double-spawn the persister", but the set is only used for shutdown drain. There is no `(session_id, run_id)` keyed lookup, no early-return on a pre-existing persister:

```python
persist_task = asyncio.create_task(_persist_events(...))
persist_task.add_done_callback(_log_persist_task_result)
pending = getattr(request.app.state, "persist_tasks", None)
if pending is None:
    pending = set()
    request.app.state.persist_tasks = pending
pending.add(persist_task)
persist_task.add_done_callback(pending.discard)
```

Two concurrent `POST /v1/sessions/{id}/stream` for the same `run_id` (e.g. a browser retry on a transient hiccup) double-subscribe the bus, double-append to the execution ring, and race two `message_uuid` dicts on the first `TEXT_MESSAGE_CONTENT`. The second insert competes against `_next_seq` retries — exactly the noise hand-traced in `repos/messages.py:_MAX_SEQ_RETRIES`.

- **Fix**: key `pending` by `(session_id, run_id)` (dict, not set). On entry, if a persister already exists for that key, do **not** spawn a second one; just subscribe a new SSE generator to the same bus topic. Either that, or hold a per-session-run `asyncio.Lock` long enough to dedup.

#### H2. `runner/hooks/audit.py:70-79` — `AuditHookSender.stop()` discards every queued `AfterToolCall`

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

`_after_queue` (max 256) is dropped on graceful shutdown. That is up to 256 missing `tool_call.completed` audit rows per runner exit — including warm-idle eviction, where shutdowns are *expected*. Audit chain becomes lossy by design.

- **Fix**: drain the queue before cancelling. Pop and `await self._post(...)` each pending payload bounded by `HOOK_DEADLINE_S * len(queue)` (≤ 8 minutes worst case; tight enough since these are sync POSTs in batch). Or expose `flush()` and call it from `runner/__main__.py:_run`'s `finally`.

#### H3. `src/api/internal.py:168-175` — `RUN_FINISHED` pop is not compare-and-swap

```python
if (frame_type in ("RUN_FINISHED", "RUN_ERROR")
        and active_by_session is not None):
    active_by_session.pop(session_id, None)
```

Same race as the prior reviewer flagged in residual #6: a new run's `RUN_STARTED` can arrive in a separate `POST /internal/sessions/{id}/events` between this handler's `bus.publish` (line 162) and the pop (line 175). The pop then erases the new run's mapping, and the new run's first intermediate frame buffers as an orphan instead of routing.

Narrow window (single-uvicorn-worker, two concurrent ingress requests during runner handover), but it is exactly the symptom of "warm-idle eviction + respawn" failures.

- **Fix**:

  ```python
  if active_by_session.get(session_id) == str(run_id):
      active_by_session.pop(session_id, None)
  ```

---

### Medium (fix if time allows)

#### M1. `src/api/stream.py:347-353` — env reads bypass the `Settings` model

```python
llm_provider = os.environ.get("LLM_PROVIDER") or settings.llm_provider
model_id_default = (
    "gemini-3.1-pro-preview"
    if llm_provider == "gemini"
    else "claude-3-5-haiku-20241022"
)
model_id = os.environ.get("LLM_MODEL_ID", model_id_default)
```

Both env vars are read directly. `Settings.llm_provider` is the validated source and now raises on missing provider key (good fix in `settings.py`), but this code path can return `"gemini"` from `LLM_PROVIDER` even when the validated `Settings.llm_provider != "gemini"` and `gemini_api_key` is `None`. The runner then receives a hydration payload that references a provider the operator never validated a key for, and the failure surfaces inside the container at first LLM call — exactly what the boot-time validator was meant to prevent.

- **Fix**: add `llm_model_id: str | None = None` (or per-provider `*_model_id`) to `Settings`. Drop the `os.environ.get` reads here in favour of `settings.llm_provider` + `settings.llm_model_id`. If a future operator-override is genuinely wanted, route it through Settings so validation runs.

#### M2. `runner/channel.py:144-216` — events between `body_iter.yield` and httpx flush are lost on reconnect

The reconnect loop replays `pending_after_break` on the next stream attempt, but only events drained from `_outbound.get_nowait()` after the exception fires. Any line that `body_iter` already yielded but httpx had not yet flushed across the TCP boundary is silently dropped — including `RUN_FINISHED`. The probability is low (transient breaks during quiet periods), but when a runner crashes mid-emit this is exactly the path that produces "missing terminal frame" symptoms.

- **Fix**: track the last frame yielded by `body_iter` in a local `last_inflight: dict | None`; on reconnect, push it back onto `pending_after_break` ahead of the queue drain. Or, switch to a sequenced ack model where the backend echoes a `received_seq` and the runner only discards from the in-flight buffer past that watermark. (Latter is a larger change; the local-tracking patch is the smaller mitigation.)

#### M3. `src/settings.py:126-136` — `allow_hmac_fallback=True` silently uses `"dev-secret-please-rotate"` as a production secret

`kloc_hook_secret` defaults to the placeholder string. The strict-mode fix in `webhooks.py` correctly rejects when no registry entry exists, but the moment `allow_hmac_fallback=True` is set (legitimate for some bootstrap flows) the fallback secret in use is whatever `kloc_hook_secret` happens to be — including the placeholder. There is no boot-time check that the placeholder is rotated.

- **Fix**: in `_validate_provider_key` (or a sibling validator), raise if `allow_hmac_fallback` is `True` and `kloc_hook_secret == "dev-secret-please-rotate"` and `stub_mode` is `False`.

---

### Low (nice to have)

#### L1. `src/api/internal.py` + `src/api/webhooks.py` — `_diag` writes one or more stderr lines per JSONL frame

`B-DIAG-EVENTS` / `B-DIAG-AUTH` lines are emitted unconditionally from every dispatch and every webhook receipt. The comment says "uvicorn filters `kloc_agent.*` INFO records", but the fix (bypass via stderr) leaves them on permanently. Under any non-trivial traffic this is a meaningful log-volume cost and obscures real signal.

- **Fix**: gate `_diag` behind `os.environ.get("KLOC_DIAG", "")` or `Settings.diag_events: bool = False`. Default off in production, on in compose dev/smoke.

#### L2. `src/main.py:83-88` — annotated-assignment-to-attribute on `app.state` discards the annotation

```python
app.state.active_run_by_session: dict[str, str] = {}
app.state.pending_pre_run_started: dict[str, list[dict]] = {}
```

This is syntactically legal but Python discards the annotation at runtime (PEP 526 only stores annotations in class/module `__annotations__`, not in object attribute assignments). The hint is misleading because static type checkers don't enforce it either. Code works, but the annotation suggests safety the language does not provide.

- **Fix**: just drop the annotations; or, if a per-state dataclass is wanted, define `class AppState: ...` and assign `app.state` slots there.

#### L3. `src/runner_mgmt/registry.py:228-242` — double-check after acquiring `spawn_lock` re-runs `is_alive`, which hits the Docker daemon

```python
async with spawn_lock:
    existing = await self._get_entry(session_id)
    if existing is not None and await self._runner.is_alive(existing.handle):
        return existing
    handle = await self._runner.spawn(hydration_payload)
```

Under high concurrency on the same session, every blocked spawner calls `is_alive` (which is a Docker inspect API call on `DockerRunner`). Minor perf cost; correctness is fine. Consider caching the alive result for ~50ms inside the entry to absorb the thundering herd, or accept this as low-frequency enough not to matter.

#### L4. `src/api/internal.py:271-283` — `ClientDisconnect` returns `202` with `{"received": count, "disconnected": True}`

Reasonable, but `received: 0` is possible (runner reopened before sending any line), and the response body would mislead a debugger into thinking 0 frames were valid. Consider distinguishing "no bytes were received" from "some frames received then disconnect" so the disconnect log line and the response payload agree.

---

## What Looks Good

- **`src/settings.py:138-156`** — the `_validate_provider_key` validator is no longer a no-op; it correctly raises for missing `anthropic_api_key` / `gemini_api_key` outside stub mode. Resolves residual #3.
- **`src/api/webhooks.py:115-135`** — strict-mode HMAC: registry-wired-but-no-entry now returns 401 *before* HMAC verify, so the bootstrap secret is never tested against an unknown runner_id. Resolves residual #4.
- **`src/runner_mgmt/registry.py:87-199`** — per-session `_spawn_locks` correctly serialize the check-then-spawn sequence without widening `_lock` (which would deadlock against `_on_evict`). The `expected_runner_id` guard in `_remove_entry` is a clean belt-and-braces against stale watcher callbacks. Resolves residual #5.
- **`runner/channel.py:123-216`** — outbound stream now reconnects with exponential backoff instead of dying on first transient close. This is the structural fix the runner needed.
- **`src/streaming/event_bus.py:23-55`** — `QueueFull` now publishes a sentinel and evicts the slow subscriber instead of silently dropping events. Prevents the "missing `RUN_FINISHED` under load" failure mode.
- **`src/repos/messages.py:41-87`** — `append` retries on `IntegrityError` from the `(session_id, seq)` UNIQUE violation up to `_MAX_SEQ_RETRIES`. Concurrent inserts now degrade to seq-collision retry instead of a 500.
- **`src/streaming/sse.py:48-67`** — unknown event types are validate-and-skip instead of crashing the whole `StreamingResponse`. Closes the previously-fatal `runner_ready` / `heartbeat` cross-contamination.
- **`src/runner_mgmt/hydrate.py`** — named-volume hydration path is the right fix for the host-path resolution bug; the `__getattr__` shim preserves import-site compatibility cleanly.

---

## Mapping back to `residual-issues.md`

| Residual finding | Status in working tree |
|---|---|
| #1 orphan-buffer flush ordering | **NOT fixed** — see C1 |
| #2 persister deduplication | **NOT fixed** — see H1 |
| #3 `_validate_provider_key` no-op | **Fixed** |
| #4 HMAC fallback enforcement | **Fixed** (via `Settings.allow_hmac_fallback`) |
| #5 `get_or_spawn` race | **Fixed** (per-session spawn locks) |
| #6 `RUN_FINISHED` pop not CAS | **NOT fixed** — see H3 |
| #7 audit drain on shutdown | **NOT fixed** — see H2 |

## Recommended merge order

1. **C1** — single-block move in `internal.py`. Unblocks the resume/cursor-replay scenario.
2. **H3** — three-line CAS in `internal.py`. Trivial.
3. **H2** — drain-then-cancel in `runner/hooks/audit.py:stop`. Bounded loop; safe to land.
4. **M1** — promote `LLM_MODEL_ID` to `Settings`. Touches `settings.py` + `stream.py`; one test.
5. **H1** — persister dedup. Largest of the open changes; touches the connection-lifetime contract, needs a reconnect test.
6. **M3 / M2 / L1** — defensive hardening. Can land separately from the bug-fix train.
