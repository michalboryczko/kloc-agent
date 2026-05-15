# Unmapped Code Review Findings

All confirmed code review findings that do **not** map to any of the 6 reported
test failures (`test-failures-root-cause-mapping.md`). These are real issues
with the `kloc-agent` codebase but are either latent (not currently flaking a
test) or orthogonal to the active failure cluster.

Verification status: `confirmed` = code defect reproduced; `partial` = real but
narrower than originally claimed.

---

## Critical

### `runner/hooks/audit.py:36` — HMAC signing algorithm differs from verifier
*(confirmed in prior review)*

`_sign()` re-encodes the body: `f"{ts_ms}.{body.decode('utf-8')}".encode("utf-8")`.
Verifier at `src/hooks_audit/verify_hmac.py:32` concatenates raw bytes:
`f"{ts}.".encode("utf-8") + body`. Diverges on non-ASCII UTF-8 JSON (e.g.,
Unicode in tool names or result previews), causing every `BeforeToolCall` event
to fail HMAC and silently block tool execution.

Fix: both must use identical byte-concatenation form — `f"{ts_ms}.".encode() + body`.

### `src/streaming/event_bus.py:24` — `put_nowait` swallows `QueueFull`
*(confirmed in prior review)*

`publish()` calls `q.put_nowait(event)` on every subscriber queue (maxsize=10 000).
`asyncio.QueueFull` is caught nowhere. A slow SSE subscriber causes the event to
disappear entirely with no error, user notification, or fallback path.

Fix: `await q.put(event)` or catch `QueueFull` and close the affected subscriber
with a log line.

---

## High

### `src/settings.py:93-100` — `get_settings()` not thread-safe
*(confirmed in prior review)*

Module-level `_settings` singleton is set without a lock. Under uvicorn multi-worker
mode or concurrent test runs, two threads can both read `_settings is None` and
each construct a `Settings()`, with the second clobbering the first.

Fix: use `functools.lru_cache` on `get_settings()` or instantiate at module level.

### `runner/__main__.py:81` — `contextlib.ExitStack` used for async MCP clients
*(confirmed in prior review)*

`build_mcp_clients` returns `MCPClient` objects that are async context managers
(need `async with`). The code uses synchronous `ExitStack` + `enter_context()`.
If `MCPClient.__enter__` is not defined, the MCP connection is never established
and `list_tools_sync()` returns empty tools — silently.

Fix: `contextlib.AsyncExitStack` + `await stack.enter_async_context(client)`.

### `src/runner_mgmt/hydrate.py:41-52` — Module-level constants read env vars at import time
*(confirmed in prior review)*

`HYDRATION_BACKEND_DIR` and `HYDRATION_VOLUME_NAME` are resolved from
`os.environ.get(...)` at module import. Tests or tooling that set these env
vars after the module loads see the old values.

Fix: read these inside the functions that use them, or defer with lazy init.

### `src/runner_mgmt/registry.py:255-263` — `get_by_runner_id` O(n) linear scan
*(confirmed in prior review)*

Every inbound event from a runner iterates all registry entries under `_lock`.
With many concurrent sessions this serializes all event ingestion.

Fix: maintain a reverse `runner_id -> session_id` dict updated in `_install_entry`
and `_remove_entry`.

### `src/db/models.py:47-78` — `AuditEventType` Literal and `AUDIT_EVENT_TYPES` frozenset duplicated
*(confirmed in prior review)*

The 12 event type strings are hardcoded in both the `Literal` type hint and a
runtime `frozenset`. These can drift silently.

Fix: derive `AUDIT_EVENT_TYPES = frozenset(typing.get_args(AuditEventType))`.

### `runner/agent_factory.py:119-122` — Model ID fallback calls `.get()` on Pydantic model
*(confirmed in prior review)*

`getattr(payload, "model_id", None) or payload.get("model_id", "")` — if `payload`
is a Pydantic model, `payload.get()` raises `AttributeError` (Pydantic models
have no `.get()`). Silent AttributeError propagates.

Fix: always use `getattr` or convert to dict first.

### `src/streaming/execution_registry.py:64` — Bare `assert` in production path
*(confirmed in prior review)*

`assert self.finished_ts is not None` is stripped under `python -O`, making the
subsequent `time.monotonic() - self.finished_ts` raise `TypeError` on `None`.

Fix: `if self.finished_ts is None: raise RuntimeError(...)`.

### `pyproject.toml:33` — `mcp` dependency unpinned
*(confirmed in prior review)*

`mcp` is unpinned. The `mcp` spec is actively developed and the codebase already
imports `mcp.client.streamable_http` (a recent addition). A breaking change to
the `mcp` package would not be caught by CI.

Fix: pin `mcp>=1.2,<2`.

---

## Medium

### `src/settings.py:58-59` — API keys default to empty string instead of `None`
*(confirmed in prior review)*

`anthropic_api_key: str = ""` and `gemini_api_key: str = ""`. Pydantic accepts
empty strings and only fails at the first LLM call. Misconfiguration is silent
until runtime.

Fix: `str | None = None`; add a `model_validator` ensuring at least one key is
set when the corresponding provider is selected.

### `tests/conftest.py:77-79` — Hardcoded absolute paths to developer machine
*(confirmed in prior review)*

`/Users/michal/dev/ai/kloc/kloc-intelligence/` and `/Users/michal/dev/ai/kloc/data/reference-fresh/sot.json`
hardcoded. Tests skip silently on any other machine.

Fix: env-var-only defaults with a clear skip message when unset.

### `runner/__main__.py:38-39` — Hydration file read has no timeout or size guard
*(confirmed in prior review)*

`json.load(f)` on a malformed or malicious hydration file hangs (named pipe misuse)
or loads an unbounded JSON blob.

Fix: set a read size limit or use `iqp` (iterative JSON parser) with a depth cap.

### `src/repos/messages.py:29-35` — `_next_seq` races under concurrent inserts
*(confirmed in prior review)*

`SELECT max(seq)+1` + separate `INSERT` is not atomic. Two concurrent requests
for the same `session_id` can read the same max, and the second INSERT violates
`uq_messages_session_seq`. The error propagates as an unhandled 500.

Fix: `INSERT ... RETURNING seq` with a sequence, or `INSERT ... ON CONFLICT DO
UPDATE` using the sequence.

### `src/streaming/debounce.py` — `_flush_after_interval` task outlives `on_end`
*(confirmed in prior review)*

When `on_end` pops the buffer and cancels `flush_task`, a race exists: if the
timer fires between `pop` and `cancel`, `_flush_after_interval` fetches `None`
from `self._buffers`. No data loss but a stale timer fires for one interval.

Fix: cancel the timer before popping the buffer; add a guard for `None` in the
timer callback.

---

## Refuted (kept for record — do not act on)

The following findings were raised in good faith by a reviewer but verified
as not representing actual defects:

- **`event_bus.py:26-41` subscribe memory leak** — `finally` block in the
  generator unconditionally removes the queue on any exit. No orphan accumulation.
- **`main.py:97-126` double DockerRunner in stub mode** — Clean `if/else`; each
  branch constructs `DockerRunner` exactly once. Misread.
- **`event_bus.py` sentinel queue not drained on subscriber death** — Same
  `finally` cleanup handles this; sentinel does not accumulate.
- **`event_bus.py:18` asyncio.Lock non-reentrant deadlock** — No coroutine in the
  file re-acquires `_lock` reentrantly. Property to be aware of, not a current bug.
- **`registry.py:150-165` duplicate runner spawn** — Window is real but the second
  spawn overwrites the entry under `_lock` (L248), leaking an orphaned container
  rather than crashing. Severity is at most Medium; the finding is noted here only
  for completeness and because it overlaps with Issue 3 in the test-failures doc.