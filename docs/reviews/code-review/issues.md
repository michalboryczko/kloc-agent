# kloc-agent — Combined Open Issues

Merged from `docs/reviews/residual-issues.md` and the 2026-05-16 code review
(`docs/reviews/code-review/2026-05-16-kloc-agent-cr.md`).

Each item carries a fixed ID, severity, exact file:line anchor, evidence, and
proposed fix. Resolved items are listed in §Closed at the bottom for
traceability.

Verification basis: line-by-line read of the changed files in
`git diff HEAD` against the `13fd93f57 WIP: kloc-agent-poc - baseline before
fix sprint` working tree.

---

## Open issues

### ISS-01 — Pre-RUN_STARTED buffer flushed before the RUN_STARTED frame
- **Severity**: Critical
- **Source**: `src/api/internal.py:117-130` (carries residual-issues #1)
- **Evidence**:
  ```python
  if frame_type == "RUN_STARTED" and active_by_session is not None:
      active_by_session[session_id] = str(run_id)
      if pending_by_session is not None:
          pending = pending_by_session.pop(session_id, None)
          if pending and bus is not None:
              for buf in pending:
                  await bus.publish(session_id, str(run_id), buf)   # buffered frames go first
  # ... fall through ...
  await bus.publish(session_id, str(run_id), frame)                  # RUN_STARTED publishes second (line 162)
  ```
  Subscribers see `[buffered..., RUN_STARTED, ...]`. Violates AG-UI lifecycle
  ordering; primary suspect for Issue 2 (resume / cursor-replay regression).
- **Fix**: publish the current `RUN_STARTED` first (line 162), then flush the
  pending buffer. Move the flush block to after the publish, gated on
  `frame_type == "RUN_STARTED"`. Add unit test: orphan → RUN_STARTED →
  terminal, assert RUN_STARTED is at index 0 on the subscriber.

### ISS-02 — Persister task is unconditionally spawned; reconnect double-subscribes the bus
- **Severity**: High
- **Source**: `src/api/stream.py:98-114` (carries residual-issues #2)
- **Evidence**:
  ```python
  persist_task = asyncio.create_task(_persist_events(...))
  pending = getattr(request.app.state, "persist_tasks", None)
  if pending is None:
      pending = set()
      request.app.state.persist_tasks = pending
  pending.add(persist_task)
  persist_task.add_done_callback(pending.discard)
  ```
  Comment claims this prevents double-spawn on reconnect, but the set is
  only used for shutdown drain — no `(session_id, run_id)` lookup. Two
  concurrent `POST /v1/sessions/{id}/stream` for the same run double-
  subscribe the bus, double-append `execution.events`, and race two
  `message_uuid` dicts on the first delta (which then competes with
  `MessageRepo._MAX_SEQ_RETRIES`).
- **Fix**: key `pending` by `(session_id, run_id)` as a dict. If a persister
  for that key already exists and is not done, do not create another;
  subscribe a fresh SSE generator onto the same bus topic instead.

### ISS-03 — `AuditHookSender.stop()` discards the AfterToolCall queue
- **Severity**: High
- **Source**: `runner/hooks/audit.py:70-79` (carries residual-issues #7)
- **Evidence**:
  ```python
  async def stop(self) -> None:
      if self._after_worker:
          self._after_worker.cancel()
          try:
              await self._after_worker
          except (asyncio.CancelledError, BaseException):
              pass
  ```
  `_after_queue` (max 256) is dropped on every graceful shutdown,
  including warm-idle eviction. Up to 256 `tool_call.completed` audit
  rows lost per runner exit.
- **Fix**: drain the queue before cancelling. Loop
  `self._after_queue.get_nowait()` and `await self._post(...)`, bounded
  by `HOOK_DEADLINE_S * len(queue)`. Or expose `flush()` and call from
  `runner/__main__.py:_run`'s `finally`.

### ISS-04 — `RUN_FINISHED` active-run pop is not compare-and-swap
- **Severity**: High
- **Source**: `src/api/internal.py:168-175` (carries residual-issues #6)
- **Evidence**:
  ```python
  if (frame_type in ("RUN_FINISHED", "RUN_ERROR")
          and active_by_session is not None):
      active_by_session.pop(session_id, None)
  ```
  If a fresh run's `RUN_STARTED` arrives in a separate ingress request
  between `bus.publish` (line 162) and this pop, the pop wipes the new
  run's mapping. The new run's first intermediate frame then buffers as
  an orphan.
- **Fix**:
  ```python
  if active_by_session.get(session_id) == str(run_id):
      active_by_session.pop(session_id, None)
  ```

### ISS-05 — `_build_hydration_payload` reads `LLM_PROVIDER`/`LLM_MODEL_ID` from env, bypassing `Settings`
- **Severity**: Medium
- **Source**: `src/api/stream.py:347-353`
- **Evidence**:
  ```python
  llm_provider = os.environ.get("LLM_PROVIDER") or settings.llm_provider
  model_id_default = (
      "gemini-3.1-pro-preview"
      if llm_provider == "gemini"
      else "claude-3-5-haiku-20241022"
  )
  model_id = os.environ.get("LLM_MODEL_ID", model_id_default)
  ```
  `Settings._validate_provider_key` now correctly raises when
  `llm_provider=gemini` and `gemini_api_key` is missing — but this code
  path can return `"gemini"` from raw env even when
  `Settings.llm_provider != "gemini"`. The runner receives a hydration
  payload referencing a provider whose key the validator never checked,
  and the failure surfaces at first LLM call inside the container.
- **Fix**: add `llm_model_id: str | None = None` (or per-provider model
  fields) to `Settings`. Drop the `os.environ.get` reads here in favour of
  `settings.llm_provider` + `settings.llm_model_id`. Any future
  operator override should route through `Settings` so the validator
  runs.

### ISS-06 — Runner reconnect loses events buffered between `body_iter.yield` and httpx flush
- **Severity**: Medium
- **Source**: `runner/channel.py:144-216`
- **Evidence**: On transport exception the `except` branch drains
  `_outbound` via `get_nowait` into `pending_after_break` for the next
  attempt — but events that `body_iter` already yielded and httpx had
  not yet flushed across the TCP boundary are lost. Probability is low,
  but it is exactly the path that produces "missing `RUN_FINISHED`"
  symptoms during a runner crash mid-emit.
- **Fix**: track the most recently yielded frame in a local
  `last_inflight: dict | None`; on reconnect, prepend it to
  `pending_after_break` before draining the queue. Or move to a
  backend-ack'd watermark.

### ISS-07 — `allow_hmac_fallback=True` silently uses placeholder secret
- **Severity**: Medium
- **Source**: `src/settings.py:86-90, 126-156`
- **Evidence**: `kloc_hook_secret` defaults to
  `"dev-secret-please-rotate"`. The strict-mode fix in
  `webhooks.py:_resolve_runner_secret` correctly rejects unknown
  runners, but the moment `allow_hmac_fallback=True` is enabled the
  fallback secret in use is whatever `kloc_hook_secret` happens to be
  — including the placeholder. There is no validator that the
  placeholder has been rotated.
- **Fix**: extend `_validate_provider_key` (or a sibling validator) to
  raise when `allow_hmac_fallback is True and
  kloc_hook_secret == "dev-secret-please-rotate"
  and not stub_mode`.

### ISS-08 — `_diag` writes one+ stderr line per JSONL frame
- **Severity**: Low
- **Source**: `src/api/internal.py:36-41` + every call site (lines 61, 71,
  84, 95, 126, 143, 149, 155, 163, 207, 218, 236, 264, 276, 285);
  also `src/api/webhooks.py:54-59` + call sites (lines 104, 121, 132,
  138, 147)
- **Evidence**: All `B-DIAG-EVENTS` / `B-DIAG-AUTH` lines are emitted
  unconditionally. Under non-trivial traffic this is meaningful
  log-volume cost and obscures real signal.
- **Fix**: gate `_diag` behind `os.environ.get("KLOC_DIAG", "")` or
  `Settings.diag_events: bool = False`. Default off in production; on
  in compose dev/smoke.

### ISS-09 — `app.state.*` annotated-assignment annotations are silently discarded
- **Severity**: Low
- **Source**: `src/main.py:83`, `src/main.py:88`
- **Evidence**:
  ```python
  app.state.active_run_by_session: dict[str, str] = {}
  app.state.pending_pre_run_started: dict[str, list[dict]] = {}
  ```
  Per PEP 526, attribute-target annotated assignments do not record the
  annotation anywhere; static checkers don't see it either. Reader is
  misled about type guarantees.
- **Fix**: drop the annotations. If structured `app.state` is desired,
  introduce an `AppState` dataclass and assign slots there.

### ISS-10 — Double-check after `spawn_lock` re-runs Docker `is_alive`
- **Severity**: Low
- **Source**: `src/runner_mgmt/registry.py:235-242`
- **Evidence**:
  ```python
  async with spawn_lock:
      existing = await self._get_entry(session_id)
      if existing is not None and await self._runner.is_alive(existing.handle):
          return existing
  ```
  Every blocked spawner for the same session hits the Docker inspect
  API on the same handle. Correctness is fine; minor thundering-herd
  cost.
- **Fix**: cache the `is_alive` result on the entry for ~50ms, or accept
  as low-frequency and leave alone.

### ISS-13 — Purge dev-coordination and historical-narrative comments from source
- **Severity**: Medium
- **Source**: 35 files (every changed `src/**/*.py` and `runner/**/*.py`); ~161 offending comments
- **Evidence**: representative samples:
  ```python
  # src/runner_mgmt/registry.py:42-46
  # BeforeToolCall webhook receipt (dev-1's src/api/webhooks.py via
  # registry.on_tool_call_started); cleared on AfterToolCall via
  # on_tool_call_completed. Used by `_on_crash` to emit
  # `tool_call.crashed` per plan §575 / AC20.

  # src/runner_mgmt/registry.py:101-108
  # Reviewer-2 R1 + team-lead loop-1 directive: refuse to transition
  # from stub-mode to real-runner mode without an `audit_emit`. The
  # `RunnerRegistry()` no-args day-1 stub remains supported (it lets
  # lifespan import-clean before dev-1's repos exist), ...

  # src/api/internal.py:36-41
  def _diag(msg: str) -> None:
      """B-DIAG-EVENTS diagnostic emitter — writes directly to stderr to
      bypass uvicorn's --log-config which silently filters `kloc_agent.*`
      INFO records in the container image. ..."""

  # runner/agent_factory.py:14-26
  B-DIAG-A root cause + fix: `ag_ui_strands.StrandsAgent` constructs a
  fresh inner `StrandsAgentCore` per thread_id ...
  ```
  Comments name developers, reviewer rounds, plan section anchors
  (`plan §575`), acceptance-criteria IDs (`AC20`), diagnostic tags
  (`B-DIAG-A`, `B-INFRA-1`), and historical "we used to do X, now we
  do Y" narrative. None of this survives the PR: identifiers rot when
  authors leave, plan/section numbers shift, and the diff that
  motivated the comment is one `git blame` away.

  Quick scope count:
  ```
  grep -rE 'dev-[0-9]|reviewer-[0-9]|plan §|B-DIAG|B-INFRA|AC[0-9]+|Phase [0-9]' \
      src/ runner/ --include='*.py' | wc -l   →  161
  ```
  affecting **35 files**.

- **Policy** (write into `CLAUDE.md` or a `CONTRIBUTING.md` to enforce
  going forward): default to no comments. A comment is only justified
  when it explains a non-obvious *why* that a future reader could not
  recover from the code itself — a hidden constraint, an invariant, a
  workaround for a specific bug with a link to the issue. Never name
  team members, plan sections, ACs, review rounds, or describe the
  history of the file.

- **Fix**:
  - Sweep the 35 listed files. For each comment / docstring:
    - If it names a person (`dev-N`, `reviewer-N`, "team-lead"),
      delete.
    - If it cites a plan/§/AC/phase anchor without standalone value,
      delete or replace with the actual invariant in plain prose.
    - If it narrates the previous implementation ("we used to ...",
      "the legacy path ..."), delete.
    - If it is a `B-DIAG-*` / `B-INFRA-*` tag explaining diagnostic
      plumbing, delete the tag prefix and keep only the operational
      sentence if needed.
  - Remaining comments must answer "why" not "what" and stand alone
    without project context.
  - Bundle as a single mechanical PR (no behaviour change) so review is
    cheap and reverts are easy.

### ISS-12 — Remove `stub` runner mode; only `docker` should ever be wired
- **Severity**: Medium
- **Source**: `src/settings.py:57-65`, `src/main.py:103-131`
- **Evidence**:
  ```python
  kloc_runner_mode: Literal["docker", "stub"] = Field(
      default="docker",
      description=(
          "B-INFRA-1: 'docker' (default) requires aiodocker + a bind-mounted "
          "/var/run/docker.sock — DockerRunner construction failure hard-fails "
          "boot. 'stub' is the CI / local-without-docker mode and tolerates "
          "missing aiodocker."
      ),
  )
  ```
  Lifespan in `src/main.py:103-131` branches on this setting and silently
  swallows `DockerRunner` construction failure in `stub` mode, leaving
  the registry rejecting every spawn — exactly the silent-degradation
  mode `B-INFRA-1` was supposed to kill on the `docker` side.
  Decision: local-dev parity is no longer a goal; the runner MUST be
  Docker in every environment.
- **Fix**:
  - Drop the `kloc_runner_mode` field from `Settings` entirely (also
    remove `KLOC_RUNNER_MODE` from `.env.example` / compose files).
  - In `src/main.py` lifespan, unconditionally
    `from src.runner_mgmt.docker_runner import DockerRunner` and call
    `runner_registry.set_runner(DockerRunner(...))`. Let an
    `ImportError` / construction failure propagate so boot fails loudly
    rather than silently entering a degraded state.
  - Audit unit/integration tests that may have been relying on
    `KLOC_RUNNER_MODE=stub` — they should now inject a fake `Runner`
    impl via `RunnerRegistry.set_runner(FakeRunner())` directly rather
    than going through settings.
  - Keep `KLOC_STUB_MODE` (the separate `stub_mode` flag at
    `src/settings.py:38-45`) — that one gates provider-key validation
    for tests and is orthogonal to runner mode.

### ISS-11 — `ClientDisconnect` response body misleads when `received == 0`
- **Severity**: Low
- **Source**: `src/api/internal.py:271-283`
- **Evidence**:
  ```python
  except ClientDisconnect:
      _diag(f"... bytes={total_bytes} chunks={chunk_count} frames={count}")
      return JSONResponse(
          content={"received": count, "disconnected": True},
          status_code=status.HTTP_202_ACCEPTED,
      )
  ```
  A reconnect that closed before sending any line still returns 202
  with `received: 0`, which reads like a successful empty ingest.
- **Fix**: distinguish "no bytes" from "some frames then disconnect" in
  the response shape, or return 200 vs 202 based on whether any frame
  was dispatched.

---

## Closed (resolved in working tree)

| ID | Origin | Status | Where it was fixed |
|---|---|---|---|
| residual #3 — `_validate_provider_key` no-op | residual-issues.md | Fixed | `src/settings.py:138-156` now raises for missing provider key outside `stub_mode` |
| residual #4 — fallback HMAC secret accepted in prod | residual-issues.md | Fixed | `src/api/webhooks.py:115-135` rejects unknown runner_id with 401 before HMAC verify; gated by `Settings.allow_hmac_fallback` |
| residual #5 — `get_or_spawn` check/spawn race | residual-issues.md | Fixed | `src/runner_mgmt/registry.py:87-199` per-session `_spawn_locks` + `expected_runner_id` guard in `_remove_entry` |
| original "single transient close kills runner" | review history | Fixed | `runner/channel.py:123-216` reconnect loop with backoff |
| `RUN_FINISHED` lost under bus QueueFull | review history | Fixed | `src/streaming/event_bus.py:23-55` evicts slow subscribers via sentinel |
| Concurrent `(session_id, seq)` UNIQUE collisions surface as 500 | review history | Fixed | `src/repos/messages.py:41-87` retries up to `_MAX_SEQ_RETRIES` |
| Unknown event types crash `StreamingResponse` | review history | Fixed | `src/streaming/sse.py:48-67` validate-and-skip |
| Host-path bind mount for hydration tmpfile | review history | Fixed | `src/runner_mgmt/hydrate.py` named-volume design |

---

## Suggested merge order

1. **ISS-01** — single-block move in `src/api/internal.py`. Unblocks the resume/cursor-replay scenario.
2. **ISS-04** — three-line CAS in `src/api/internal.py`. Trivial.
3. **ISS-03** — drain-then-cancel in `runner/hooks/audit.py:stop`. Bounded loop.
4. **ISS-05** — promote `LLM_MODEL_ID` to `Settings`. Touches `settings.py` + `stream.py`.
5. **ISS-12** — remove `stub` runner mode. Clarifies boot contract; touches `settings.py` + `main.py` + tests.
6. **ISS-02** — persister dedup. Largest of the open bugs; needs a reconnect test.
7. **ISS-07 / ISS-06 / ISS-08** — defensive hardening; can land separately.
8. **ISS-13** — comment sweep across 35 files. Mechanical, no behaviour change; run as a single PR.
9. **ISS-09 / ISS-10 / ISS-11** — cleanup; bundle with the next refactor.

---

## Test-failure mapping (carries forward from residual-issues.md §Mapping)

| Original test failure | Likely contributor among open issues |
|---|---|
| Issue 1 — concurrent session cross-talk | None of the open issues cited. Capture fresh traceback. |
| Issue 2 — resume / cursor-replay regression | **ISS-01** (primary) |
| Issue 3 — warm-idle eviction + respawn | **ISS-04** (handover race). Residual #5 is now fixed. |
| Issue 4 — vertical-slice `RUN_FINISHED` missing | **ISS-06** (channel reconnect loses inflight frame) is a candidate. |
| Issue 5 — rehydrate / same_chat | No identified contributor. |
| Issue 6 — backend `ClientDisconnect` noise | Now handled at `src/api/internal.py:271`; should be quiet. |
