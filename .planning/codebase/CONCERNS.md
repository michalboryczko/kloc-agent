# Codebase Concerns

**Analysis Date:** 2026-05-15

## Tech Debt

**Dev-coordination comment pollution (ISS-13):**
- Issue: ~161 lines across 35 source files reference developer names (`dev-1`, `reviewer-2`), plan section anchors (`plan §575`), acceptance-criteria IDs (`AC20`), and diagnostic tags (`B-DIAG-A`, `B-INFRA-1`). These identifiers rot immediately as authors leave and plan sections shift.
- Files: every changed file under `src/**/*.py` and `runner/**/*.py`, e.g. `src/runner_mgmt/registry.py:42-46`, `src/api/internal.py:36-41`, `runner/agent_factory.py:14-26`
- Impact: misleads maintainers; all context references will be stale within weeks
- Fix approach: mechanical sweep — delete any comment that names a person, cites a plan/AC/phase anchor, or narrates prior implementations. Keep only "why" comments that stand alone.

**Stub runner mode still wired (ISS-12):**
- Issue: `kloc_runner_mode: Literal["docker", "stub"]` in `src/settings.py:57-65` allows stub mode which silently swallows `DockerRunner` construction failure in `src/main.py:103-131`, leaving the registry rejecting every spawn with no loud failure.
- Files: `src/settings.py`, `src/main.py`
- Impact: degraded mode is indistinguishable from healthy at boot; all session-stream paths fail silently
- Fix approach: drop `kloc_runner_mode` entirely; unconditionally import and construct `DockerRunner`; let `ImportError` propagate loudly

**`LLM_PROVIDER`/`LLM_MODEL_ID` bypass Settings (ISS-05):**
- Issue: `src/api/stream.py:347-353` reads `LLM_PROVIDER` and `LLM_MODEL_ID` via raw `os.environ.get()`, bypassing `Settings` and its boot-time provider-key validator. A runner can be launched with a provider whose API key was never validated.
- Files: `src/api/stream.py`
- Impact: misconfiguration surfaces at first LLM call inside the container, not at boot
- Fix approach: add `llm_model_id` to `Settings`; remove the raw `os.environ.get` reads; route through `settings.llm_provider` and `settings.llm_model_id`

**`ExecutionRegistry.gc()` is never called:**
- Issue: `src/streaming/execution_registry.py:90-95` exposes a `gc()` method with a 5-minute TTL eviction, but no background task or lifespan hook calls it. Completed executions accumulate indefinitely in memory.
- Files: `src/streaming/execution_registry.py`, `src/main.py`
- Impact: memory leak on long-running backends with many sessions; each execution holds up to 10,000 events (ring cap)
- Fix approach: schedule a periodic `execution_registry.gc()` call in the lifespan (e.g., every 60 seconds)

**`app.state` annotated-assignment annotations silently discarded (ISS-09):**
- Issue: `src/main.py:83,88` uses annotated assignments on `app.state` attributes (`app.state.active_run_by_session: dict[str, str] = {}`). Per PEP 526, annotations on attribute targets are not recorded anywhere; static checkers cannot see them.
- Files: `src/main.py`
- Impact: misleads readers about type guarantees; no static analysis coverage
- Fix approach: introduce an `AppState` dataclass and assign it to `app.state`, or drop the annotations

**`strands_agentskills` pinned to a git commit hash:**
- Issue: `pyproject.toml:31` installs `strands_agentskills` from a raw GitHub commit hash (`c5564fcd2e7c249ec57b32027ffbea49e9abeb7b`) rather than a PyPI release. The repo is an AWS sample project with no stability guarantees.
- Files: `pyproject.toml`
- Impact: build is fragile if GitHub repo is renamed, archived, or commit is force-pushed; no dependency auditing via PyPI advisories
- Fix approach: vendor the code into the repo, or pin to a tagged release once the library stabilises

## Known Bugs

**ISS-01 — Pre-RUN_STARTED buffer flushed before RUN_STARTED itself:**
- Symptoms: SSE subscribers receive buffered intermediate frames before `RUN_STARTED`; downstream resume/cursor-replay fails because AG-UI lifecycle ordering is violated
- Files: `src/api/internal.py:117-130`
- Trigger: runner reconnect sends non-lifecycle frames that arrive before the session's first `RUN_STARTED`; the orphan buffer is flushed first and `RUN_STARTED` is published second
- Workaround: none; this is the primary suspected cause of the resume/cursor-replay test regression

**ISS-02 — Persister task is unconditionally spawned; reconnect double-subscribes:**
- Symptoms: two concurrent `POST /v1/sessions/{id}/stream` for the same run create two `_persist_events` tasks. Both subscribe to the event bus, double-append events into the execution ring buffer, and race on `message_uuid` dict, causing duplicate assistant rows and `_MAX_SEQ_RETRIES` exhaustion.
- Files: `src/api/stream.py:98-114`
- Trigger: browser reconnect during an active run that re-POSTs to the stream endpoint
- Workaround: none; the comment claiming reconnect-safety is incorrect

**ISS-03 — `AuditHookSender.stop()` discards the AfterToolCall queue:**
- Symptoms: up to 256 `tool_call.completed` audit rows lost per runner exit; audit trail is incomplete
- Files: `runner/hooks/audit.py:70-79`
- Trigger: any graceful shutdown — including warm-idle eviction — cancels `_after_worker` without draining `_after_queue`
- Workaround: none

**ISS-04 — `RUN_FINISHED` active-run pop is not compare-and-swap:**
- Symptoms: if a fresh run's `RUN_STARTED` arrives between the `bus.publish` and the `active_by_session.pop`, the new run's mapping is wiped. Subsequent frames buffer as orphans, causing missing events.
- Files: `src/api/internal.py:168-175`
- Trigger: concurrent ingress requests during runner reconnect handover or back-to-back runs
- Workaround: none; three-line fix is available

**ISS-06 — Runner reconnect loses events between `body_iter` yield and httpx flush:**
- Symptoms: events that `body_iter` yielded but httpx had not yet flushed are lost on transport exception; most visible as missing `RUN_FINISHED` frames
- Files: `runner/channel.py:144-216`
- Trigger: backend closes the chunked POST connection mid-stream while the runner is emitting events
- Workaround: the reconnect loop replays `_outbound` queue contents, but not the in-flight frame that was already yielded

## Security Considerations

**No authentication on any API endpoint:**
- Risk: all REST endpoints (`/v1/sessions`, `/v1/sessions/{id}/stream`, `/v1/webhooks/runners/...`) are unauthenticated. Any process with network access to the backend can read all sessions, inject messages, or register fake artifact webhooks.
- Files: `src/api/sessions.py:30-31`, `src/api/internal.py:6`, `src/main.py`
- Current mitigation: `internal` routes are exposed only over the compose bridge network; `v1` routes rely on `KLOC_CORS_ALLOW_ORIGINS` for browser-origin restriction only
- Recommendations: add a bearer-token or mTLS layer before any non-localhost exposure; the hardcoded `HARDCODED_ANALYST_ID = "analyst-poc"` in `src/api/sessions.py:31` must be replaced with real identity before multi-user deployment

**ISS-07 — `allow_hmac_fallback=True` uses placeholder secret:**
- Risk: `kloc_hook_secret` defaults to `"dev-secret-please-rotate"`. When `allow_hmac_fallback=True`, the bootstrap secret is used for any `runner_id` not in the registry — including this placeholder. Any actor who knows the placeholder can authenticate as an arbitrary runner.
- Files: `src/settings.py:86-90`, `src/api/webhooks.py`
- Current mitigation: `allow_hmac_fallback` defaults to `False` (strict mode); placeholder is only dangerous when the flag is enabled
- Recommendations: add a validator that raises when `allow_hmac_fallback is True and kloc_hook_secret == "dev-secret-please-rotate" and not stub_mode`

**Hydration files contain LLM API keys and runner secrets:**
- Risk: `write_hydration_tempfile()` writes the full `HydrationPayload` (including `runner_secret`) to `/tmp/kloc-hydration/<runner_id>.json` on the shared named volume. All runner containers mount this volume read-only and can read each other's hydration files.
- Files: `src/runner_mgmt/hydrate.py:85-103`, `src/runner_mgmt/docker_runner.py:101-107`
- Current mitigation: `chmod 0o600` is applied; the named volume is not mounted to untrusted containers
- Recommendations: delete hydration files immediately after the runner reads them, rather than only on `terminate()`; or use Docker secrets instead of a shared volume

**`/internal` routes have no authentication:**
- Risk: `POST /internal/sessions/{id}/events` and `GET /internal/sessions/{id}/inbox` accept unauthenticated JSONL/long-poll connections. Any process that can reach the backend (including other runner containers) can inject arbitrary AG-UI frames.
- Files: `src/api/internal.py:1-9`
- Current mitigation: PoC relies on compose network isolation only
- Recommendations: add per-runner token validation mirroring the HMAC webhook pattern

## Performance Bottlenecks

**Unbounded message history load at each spawn:**
- Problem: `_build_hydration_payload` in `src/api/stream.py:316` loads up to 10,000 messages from Postgres on every runner spawn. Long-lived sessions with many messages cause proportionally large payloads, slow spawns, and large hydration files on disk.
- Files: `src/api/stream.py:303-370`, `src/repos/messages.py`
- Cause: `limit=10_000` with no truncation strategy; all messages are serialised into the hydration JSON
- Improvement path: limit to the most recent N messages (e.g., last 100); implement rolling summarisation or a separate summary field in `HydrationPayload`

**`_next_seq` SELECT max + retry-on-collision is O(retries) under concurrency:**
- Problem: `MessageRepo.append()` in `src/repos/messages.py:33-87` computes `seq` via `SELECT max(seq)+1` and retries up to `_MAX_SEQ_RETRIES=5` times on UNIQUE violation. Under high concurrent message volume, retries accumulate and latency spikes.
- Files: `src/repos/messages.py`
- Cause: optimistic concurrency without a DB-native sequence
- Improvement path: use a PostgreSQL sequence (`CREATE SEQUENCE`) per session, or a `FOR UPDATE` lock on the sequence row

**`is_alive()` Docker inspect call on every spawn-lock check (ISS-10):**
- Problem: multiple concurrent `get_or_spawn` callers for the same session each call `self._runner.is_alive(existing.handle)` which issues a `GET /containers/{id}/json` to the Docker daemon. Low impact at current scale but creates a minor thundering herd.
- Files: `src/runner_mgmt/registry.py:235-242`
- Cause: no caching of `is_alive` result between competing callers
- Improvement path: cache the result with a ~50ms TTL, or accept as low-frequency

**EventBus queues grow to 10,000 events per subscriber:**
- Problem: `EventBus.register()` in `src/streaming/event_bus.py:57-68` creates queues with `maxsize=10_000`. Under slow SSE consumers or reconnect storms, memory per session-run pair grows to the cap before the slow-subscriber sentinel logic kicks in.
- Files: `src/streaming/event_bus.py`
- Cause: generous cap chosen to absorb bursty runner output; no back-pressure to the runner
- Improvement path: reduce queue cap and tune sentinel eviction for typical event volumes

## Fragile Areas

**AG-UI event ordering (`src/api/internal.py`):**
- Files: `src/api/internal.py`
- Why fragile: the `active_run_by_session` dict is process-local and mutated across concurrent async requests without locks. Three known races: ISS-01 (orphan flush before `RUN_STARTED`), ISS-04 (non-CAS pop on `RUN_FINISHED`), and the pre-`RUN_STARTED` buffer losing its bound when `pending_by_session` is `None`.
- Safe modification: any change to `_dispatch_frame` must reason carefully about the ordering of `active_by_session` writes relative to `bus.publish` calls. Add a unit test asserting `RUN_STARTED` is always the first event subscribers see.
- Test coverage: `tests/unit/test_internal.py` covers dispatch routing but not ordering invariants under concurrent requests

**Persist task deduplication (`src/api/stream.py`):**
- Files: `src/api/stream.py:98-114`
- Why fragile: `app.state.persist_tasks` is a `set` keyed by task object, not by `(session_id, run_id)`. Reconnects unconditionally spawn a second persister. Any change to the reconnect path must audit whether a persister already exists.
- Safe modification: convert `persist_tasks` to a `dict[tuple[str, str], asyncio.Task]` before extending reconnect logic
- Test coverage: no test exercises the reconnect-double-spawn path

**Runner registry concurrency (`src/runner_mgmt/registry.py`):**
- Files: `src/runner_mgmt/registry.py`
- Why fragile: dual-lock design (`_lock` for registry dict, `_spawn_locks` for per-session spawn serialisation) with documented deadlock avoidance invariant (must not hold `_lock` while awaiting kill task). Breaking this invariant causes a deadlock. Any change to lock acquisition order requires careful review.
- Safe modification: read the concurrency model docstring at the top of the file before modifying any lock-acquiring path; add the `expected_runner_id` guard when removing entries from callbacks
- Test coverage: `tests/unit/test_registry_concurrent_spawn.py` covers concurrent spawn; `tests/unit/test_registry.py` covers lifecycle

**`_diag()` unconditional stderr writes (`src/api/internal.py`, `src/api/webhooks.py`) (ISS-08):**
- Files: `src/api/internal.py:36-41` and all call sites, `src/api/webhooks.py:54-59` and all call sites
- Why fragile: every JSONL frame and every webhook produces multiple `_diag` lines to stderr unconditionally. Under production traffic this saturates container logs, obscures real signal, and incurs I/O overhead per event.
- Safe modification: gate `_diag` behind an env flag (`KLOC_DIAG`) before increasing traffic volume
- Test coverage: none; `_diag` is not tested

## Scaling Limits

**Single uvicorn worker:**
- Current capacity: one uvicorn worker process; all in-process state (`event_bus`, `execution_registry`, `runner_registry`, `active_run_by_session`) is process-local
- Limit: any second worker sees empty registries and cannot route frames from its sessions; horizontal scaling is broken by design
- Scaling path: move `event_bus`, `execution_registry`, and `active_run_by_session` to a shared broker (Redis pub/sub or similar); the `runner_registry` requires a distributed lock

**Docker socket bind-mounted into the backend:**
- Current capacity: one backend container controlling the Docker daemon for runner lifecycle
- Limit: the backend process holds the Docker socket; it is a single point of failure for all runner spawns. A backend restart kills all in-flight runners (handled by boot-time orphan sweep, but sessions are disrupted).
- Scaling path: separate runner lifecycle into a dedicated runner-management service; use a container orchestrator (Kubernetes, ECS) rather than direct Docker API calls

## Dependencies at Risk

**`strands_agentskills` from a GitHub commit hash:**
- Risk: installed directly from `github.com/aws-samples/sample-strands-agents-agentskills@c5564fcd2e7c249ec57b32027ffbea49e9abeb7b`; not on PyPI; no security advisory coverage; AWS sample repos are not stability-guaranteed
- Impact: build breaks if the repository is reorganised; security vulnerabilities go undetected
- Migration plan: vendor into the repo under `vendor/` or wait for a stable PyPI release

**`ag-ui-protocol==0.1.18` and `ag_ui_strands==0.1.8` — pre-1.0 SDKs:**
- Risk: both packages are pre-1.0; breaking changes are expected. The adapter in `runner/agent_factory.py` already documents one silent bug (`StrandsAgent` drops `hooks` from the seed agent) that required the `HookProvider` workaround.
- Impact: each upstream SDK release may require non-trivial adapter changes; the `# type: ignore` suppressions in `src/streaming/sse.py:20-29` and `src/streaming/agui_event_formatter.py:21-23` mean type errors from API changes go undetected
- Migration plan: enable strict type checking on these import sites; pin to a patch range (`~=0.1.18`) and review release notes on each bump

**`opentelemetry-distro[otlp]` unpinned:**
- Risk: `pyproject.toml` deliberately leaves OTel unpinned ("pin if uv lock fails"). OTel auto-instrumentation patches `httpx`, `sqlalchemy`, and `asyncpg` at import time; a breaking patch release can break those integrations silently.
- Impact: non-deterministic builds; breakage discovered only when a new lock is generated
- Migration plan: pin to a specific version range in `pyproject.toml` and update on a schedule

## Missing Critical Features

**No authentication or authorisation:**
- Problem: every `/v1/*` endpoint is unauthenticated. All sessions and messages are readable/writable by any client with network access.
- Blocks: multi-user deployment; any exposure beyond a single-operator local stack

**`session_closed` state not propagated to runners:**
- Problem: `POST /v1/sessions/{id}/close` in `src/api/sessions.py:278-300` marks the session closed in Postgres and emits an audit event, but does not terminate or drain any running runner container for that session. The runner continues running until warm-idle eviction.
- Blocks: clean session lifecycle; session close is misleading if the runner is still active
- Files: `src/api/sessions.py`, `src/runner_mgmt/registry.py`

**No rate limiting on stream endpoint:**
- Problem: `POST /v1/sessions/{id}/stream` spawns a Docker container on each call if none exists. A client that calls this endpoint rapidly can exhaust Docker resources or the DB connection pool.
- Blocks: any production deployment
- Files: `src/api/stream.py`, `src/runner_mgmt/registry.py`

## Test Coverage Gaps

**Resume/cursor-replay path (ISS-01 contributor):**
- What's not tested: concurrent requests where `RUN_STARTED` arrives after intermediate frames; ordering invariants on the `EventBus` subscriber queue
- Files: `src/api/internal.py`, `tests/unit/test_internal.py`
- Risk: the replay regression (Issue 2 in test-failure mapping) is caused by an ordering bug with no covering unit test
- Priority: High

**Reconnect double-persister (ISS-02):**
- What's not tested: two concurrent `POST /v1/sessions/{id}/stream` calls for the same run
- Files: `src/api/stream.py`, `tests/unit/test_stream.py`, `tests/integration/test_message_streaming.py`
- Risk: duplicate assistant rows in Postgres silently; detected only via manual inspection or failing e2e tests
- Priority: High

**`AuditHookSender` shutdown drain (ISS-03):**
- What's not tested: `AfterToolCall` queue contents on graceful runner shutdown; audit row count after eviction
- Files: `runner/hooks/audit.py`, `tests/unit/test_channel.py`
- Risk: audit completeness assertion in QA scenarios may pass because the test doesn't verify post-shutdown row counts
- Priority: Medium

**Security boundary — unauthenticated endpoints:**
- What's not tested: any attempt to access sessions belonging to a different analyst; no auth layer to test against
- Files: `src/api/sessions.py`, `src/api/stream.py`
- Risk: a future auth layer may have logic bugs that are never caught by existing tests
- Priority: Medium

---

*Concerns audit: 2026-05-15*
