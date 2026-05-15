# Testing Patterns

**Analysis Date:** 2026-05-15

## Test Framework

**Runner:**
- pytest 8.3+ (Python backend)
- Config: `pyproject.toml` `[tool.pytest.ini_options]`
- No frontend test framework detected (no jest.config, no vitest.config)

**Async support:**
- `pytest-asyncio>=0.24` with `asyncio_mode = "auto"` — all `async def` test functions run automatically without `@pytest.mark.asyncio` on unit tests (integration/e2e still use the decorator for clarity)

**Coverage:**
- `pytest-cov>=5.0` installed

**Run Commands:**
```bash
# Unit + integration (default: e2e excluded)
uv run pytest

# Specific tier
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m e2e          # requires compose stack + LLM API key

# Coverage
uv run pytest --cov=src --cov-report=html

# Single file
uv run pytest tests/unit/test_registry.py -v
```

## Test Tiers (Markers)

Four markers are registered in both `pyproject.toml` and `conftest.py:pytest_configure`:

| Marker | Description | Dependencies |
|--------|-------------|--------------|
| `unit` | Pure Python, no IO, no network | None — runs everywhere |
| `integration` | Real Postgres + in-process FastAPI; runner + LLM mocked | Postgres + MinIO |
| `e2e` | Full Docker Compose stack + real LLM + real Docker runner | compose stack, LLM API key |
| `slow` | Takes >30s; usually e2e | Combined with `e2e` |

Default `addopts = "-ra --strict-markers -m 'not e2e'"` — e2e is always excluded from default runs.

## Test File Organization

**Location:** Separate `tests/` directory (NOT co-located with `src/`)

```
tests/
├── conftest.py              # All shared fixtures
├── unit/                    # pytestmark = pytest.mark.unit
│   ├── test_registry.py
│   ├── test_warm_idle.py
│   ├── test_hmac.py
│   ├── test_event_bus.py
│   ├── test_settings.py
│   └── ...
├── integration/             # pytestmark = pytest.mark.integration
│   ├── test_sessions_api.py
│   ├── test_message_streaming.py
│   └── ...
├── e2e/                     # pytestmark = [pytest.mark.e2e, pytest.mark.slow]
│   ├── test_vertical_slice.py
│   ├── test_concurrent_sessions.py
│   └── ...
└── fixtures/
    ├── audit_events.py      # Final string constants for all 12 audit event names
    └── sse_client.py        # AG-UI SSE parser + collect helpers
```

**Naming:**
- Test files: `test_<module_name>.py` — mirrors implementation module name
- Test functions: `test_<what_it_tests>` or `test_<ac_number>_<description>`
- Helper classes in tests: `Fake<InterfaceName>` — `FakeRunner`, `FakeHandle`, `FakeRunnerHandle`

## Test Structure

**Module-level marker (always present in every test file):**
```python
pytestmark = pytest.mark.unit
# or
pytestmark = pytest.mark.integration
# or
pytestmark = [pytest.mark.e2e, pytest.mark.slow]
```

**Unit test pattern:**
```python
"""Docstring with regression description and fix.

References: AC number, plan §line, reviewer comment ID.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_field_reads_pydantic_attribute() -> None:
    class FakePyd:
        model_id = "claude"

    assert _field(FakePyd(), "model_id") == "claude"


async def test_ac15_kill_mid_flight_respawns_fresh():
    """AC15: warm-idle kill is mid-flight; describe the exact race."""
    runner = SlowFakeRunner(terminate_delay_s=0.05)
    registry = RunnerRegistry(runner=runner, warm_idle_s=0.01)
    ...
    assert runner.spawn_count == 2, "expected a fresh spawn after kill"
```

**Integration test pattern:**
```python
pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_create_session_returns_201_with_uuid(
    asgi_client, db_session, truncate_all_tables
):
    """POST /v1/sessions persists a row and returns the session_id (AC1)."""
    resp = await asgi_client.post("/v1/sessions", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    ...
```

**E2E test pattern:**
```python
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

@pytest.mark.asyncio
async def test_scenario_01_vertical_slice(
    anthropic_api_key, kloc_intelligence_path, sot_json_fixture,
    compose_stack, async_http_client, sse_helpers,
    db_session, truncate_all_tables,
):
    """Real-usage E2E: one message touches every PoC success criterion."""
    ...
```

## Mocking and Fakes

**Pattern:** Protocol-matching `Fake*` classes (not `unittest.mock.MagicMock`)

All fakes implement the same `Protocol` interface as the real object. Every method call is recorded in a list for assertion.

**FakeRunner pattern (in `conftest.py` and per-test-file):**
```python
class FakeRunner:
    """In-memory Runner Protocol impl. Records every call for assertion."""

    def __init__(self, outbound_events=None):
        self.spawn_calls: list[Any] = []
        self.send_calls: list[tuple[Any, dict]] = []
        self.terminate_calls: list[Any] = []
        self.outbound_events: list[dict] = outbound_events or []

    async def spawn(self, payload) -> FakeRunnerHandle:
        self.spawn_calls.append(payload)
        ...

    async def terminate(self, handle) -> None:
        self.terminate_calls.append(handle)
        handle._dead = True
```

**SlowFakeRunner pattern** (for concurrency/timing tests):
```python
class SlowFakeRunner:
    def __init__(self, terminate_delay_s: float = 0.0) -> None:
        self._terminate_delay = terminate_delay_s
        self.spawn_count = 0
        self.terminate_count = 0

    async def terminate(self, handle):
        if self._terminate_delay:
            await asyncio.sleep(self._terminate_delay)
        handle.dead = True
        self.terminate_count += 1
```

**Audit emit fakes:**
```python
events: list[tuple[str, dict]] = []

async def audit_emit(event_type: str, payload: dict) -> None:
    events.append((event_type, payload))

registry = RunnerRegistry(runner=runner, audit_emit=audit_emit)
```

**`monkeypatch` for Settings tests:**
```python
def test_api_keys_default_to_none(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.chdir(tmp_path)
    s = Settings(_env_file=None)
    assert s.anthropic_api_key is None
```

**What NOT to mock:**
- Real Postgres in integration tests — `db_session` fixture connects to an actual test DB
- The FastAPI app itself — `asgi_client` uses `httpx.ASGITransport` for in-process testing

## Fixtures (conftest.py)

All shared fixtures live in `tests/conftest.py`. Key fixtures:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `anthropic_api_key` | session | Checks `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`; skips e2e if absent |
| `kloc_intelligence_path` | session | Requires `KLOC_INTELLIGENCE_PATH` env var; skips if unset |
| `backend_url` | session | `BACKEND_URL` env var, default `http://localhost:8000` |
| `db_session` | function | Async SQLAlchemy session; skips if Postgres unreachable |
| `truncate_all_tables` | function | Wipes 4 tables before AND after each test |
| `app_in_process` | function | FastAPI with full lifespan started; skips on infra unreachable |
| `asgi_client` | function | `httpx.AsyncClient` with `ASGITransport` for in-process HTTP |
| `async_http_client` | function | `httpx.AsyncClient` bound to remote backend (e2e) |
| `mock_runner` | function | Returns `FakeRunner()` |
| `sse_helpers` | function | Exposes `tests.fixtures.sse_client` module |
| `compose_stack` | session | Asserts compose stack healthy; skips if not (does NOT start it) |

**Skip pattern for missing infrastructure:**
```python
@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[Any]:
    try:
        async with engine.connect() as conn:
            await conn.execute(_text("SELECT 1"))
    except (OperationalError, InterfaceError, OSError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres unreachable at {url}: {exc}")
```

This pattern ensures unit tests always pass in CI without a database, while integration tests skip gracefully.

## Test Data Fixtures

**Audit event vocabulary:** `tests/fixtures/audit_events.py`

```python
from typing import Final

SESSION_OPENED: Final = "session_opened"
RUNNER_SPAWNED: Final = "runner_spawned"
TOOL_CALL_STARTED: Final = "tool_call.started"
# ... all 12 event names

ALL_EVENTS: Final[frozenset[str]] = frozenset({...})
```

Import these constants instead of raw strings in assertions:
```python
from tests.fixtures.audit_events import RUNNER_SPAWNED, TOOL_CALL_STARTED
assert RUNNER_SPAWNED in audit_types
```

**SSE helpers:** `tests/fixtures/sse_client.py`

```python
# Collect all events from a stream until RUN_FINISHED or RUN_ERROR
events = await sse_helpers.collect_until(
    async_http_client,
    "POST",
    f"/v1/sessions/{session_id}/stream",
    json=body,
    stop_types=("RUN_FINISHED", "RUN_ERROR"),
    timeout=300.0,
)
sse_helpers.assert_run_completed(events)
```

**JSON fixtures:** `tests/fixtures/hydration_payload_sample.json` loaded via `conftest.py:hydration_payload_sample` fixture.

## Coverage

**Requirements:** No minimum enforced in `pyproject.toml`

**Exclusions observed:**
- `# pragma: no cover - defensive` on unreachable exception handlers in `src/main.py`
- `# pragma: no cover` on stub fixtures that skip themselves in `conftest.py`

**View Coverage:**
```bash
uv run pytest --cov=src --cov-report=html
open htmlcov/index.html
```

## Test Types

**Unit Tests (`tests/unit/`):**
- Scope: pure Python, no DB, no network, no Docker
- Test individual classes/functions in isolation using `Fake*` collaborators
- Verify concurrency invariants using `asyncio.sleep()` with short durations (0.01s–0.15s)
- Use `pytest.raises(ExcType, match="pattern")` for error path assertions

**Integration Tests (`tests/integration/`):**
- Scope: real Postgres + MinIO + in-process FastAPI app; runner stubbed with `FakeRunner`
- Use `asgi_client` (httpx + ASGITransport) for HTTP calls
- Use `truncate_all_tables` fixture to isolate state
- Inspect DB state directly via `db_session` after HTTP calls to prove commit semantics

**E2E Tests (`tests/e2e/`):**
- Scope: full Docker Compose stack with real LLM + real Docker runner
- Use `async_http_client` (httpx over network, not ASGI)
- Consume SSE streams via `sse_helpers.collect_until()`
- Require env vars: `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`, `KLOC_INTELLIGENCE_PATH`, `SOT_JSON_FIXTURE`
- Operator must run `make e2e-up` before running these

## Common Patterns

**Async Testing:**
```python
# asyncio_mode = "auto" means no decorator needed for unit tests
async def test_get_or_spawn_reuses_live_container():
    runner = SlowFakeRunner()
    registry = RunnerRegistry(runner=runner, warm_idle_s=60.0)
    entry1 = await registry.get_or_spawn("s1", {})
    entry2 = await registry.get_or_spawn("s1", {})
    assert entry1 is entry2
    await registry.shutdown_all()  # always clean up asyncio tasks
```

**Timing-based concurrency tests:**
```python
async def test_ac15_kill_mid_flight_respawns_fresh():
    runner = SlowFakeRunner(terminate_delay_s=0.05)
    registry = RunnerRegistry(runner=runner, warm_idle_s=0.01)
    e1 = await registry.get_or_spawn("s1", {})
    e1.warm_idle.on_run_finished()
    await asyncio.sleep(0.02)  # past timer expiry, into terminate
    e2 = await asyncio.wait_for(registry.get_or_spawn("s1", {}), timeout=1.0)
    assert runner.spawn_count == 2
```

**Error Testing:**
```python
def test_set_runner_requires_audit_emit():
    registry = RunnerRegistry()
    with pytest.raises(ValueError, match="audit_emit required"):
        registry.set_runner(runner)
```

**Log assertion:**
```python
async def test_log_persist_task_result_logs_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="src.api.stream"):
        _log_persist_task_result(task)
    assert any("_persist_events_failed" in r.message for r in caplog.records)
```

**Event ordering assertion (SSE/E2E):**
```python
types = [e.get("type") for e in events]
assert "RUN_STARTED" in types
assert "TOOL_CALL_START" in types
assert "TEXT_MESSAGE_START" in types
# Assert no RUN_ERROR appeared
sse_helpers.assert_run_completed(events)
```

**DB state verification after HTTP call:**
```python
# Prove commit (not just flush) by re-querying with a fresh session
db_session.expire_all()
r = await db_session.execute(
    select(Message).where(Message.session_id == uuid.UUID(session_id))
)
row = r.scalar_one()
assert row.content == "find handlers"
assert row.finalized_at is not None
```

**Always cleanup asyncio tasks:**
- Every test that creates a `RunnerRegistry` calls `await registry.shutdown_all()` at the end
- Fixture teardown via `finally` blocks in `AsyncIterator` fixtures

---

*Testing analysis: 2026-05-15*
