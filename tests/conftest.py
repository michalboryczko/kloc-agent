"""Shared pytest configuration and fixtures for the kloc-agent test suite.

Conventions (see `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §2):

- `unit` tests: no IO, no network.
- `integration` tests: real Postgres + in-process backend; mocked Runner + LLM + MCP.
- `e2e` tests: full compose stack + real Anthropic + real Docker runner.

Real fixtures (dev-1 + dev-2 shipped):
- `db_session` — async SQLAlchemy session bound to the test DB
- `truncate_all_tables` — clears all 4 tables between tests
- `mock_runner` — observable Runner Protocol impl
- `app_in_process` + `asgi_client` — drive the FastAPI app in-process

Still stubs:
- `mock_model_provider`, `mock_tools` — only needed for unit tests of runner/agent_factory
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Environment / discovery fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anthropic_api_key() -> str:
    """Yield an LLM API key for the backend's configured provider, or skip.

    The fixture name is `anthropic_api_key` for historical reasons (spec
    originally pinned AnthropicModel). The PoC deployment uses Gemini per
    team-lead deviation note; accept either `ANTHROPIC_API_KEY` or
    `GEMINI_API_KEY`. The backend's `LLM_PROVIDER` setting decides which
    one it actually uses — this fixture's only job is to gate e2e tests
    that need a live LLM behind ANY key being set.
    """
    for env in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        key = os.environ.get(env)
        if key:
            return key
    pytest.skip(
        "no LLM API key set (ANTHROPIC_API_KEY or GEMINI_API_KEY) — "
        "e2e tests require real LLM access"
    )


@pytest.fixture(scope="session")
def kloc_intelligence_path() -> Path:
    """Path to the local checkout of kloc-intelligence (MCP server source).

    Set `KLOC_INTELLIGENCE_PATH` to override; tests skip when unset to
    avoid being tied to one developer's filesystem layout."""
    raw = os.environ.get("KLOC_INTELLIGENCE_PATH")
    if not raw:
        pytest.skip(
            "KLOC_INTELLIGENCE_PATH unset — point it at a local "
            "kloc-intelligence checkout to run this test"
        )
    p = Path(raw)
    if not p.is_dir():
        pytest.skip(f"kloc-intelligence not found at {p}")
    return p


@pytest.fixture(scope="session")
def sot_json_fixture() -> Path:
    """Small sot.json fixture for the MCP to load.

    Set `SOT_JSON_FIXTURE` to a generated sot.json. Tests skip when
    unset rather than depending on developer-machine layouts.
    """
    raw = os.environ.get("SOT_JSON_FIXTURE")
    if not raw:
        pytest.skip(
            "SOT_JSON_FIXTURE unset — point it at a generated sot.json "
            "to run tests that need a code-graph fixture"
        )
    p = Path(raw)
    if not p.is_file():
        pytest.skip(f"sot.json not found at {p}")
    return p


@pytest.fixture(scope="session")
def backend_url() -> str:
    """Base URL of the backend under test."""
    return os.environ.get("BACKEND_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def hydration_payload_sample() -> dict[str, Any]:
    """Load the canonical `HydrationPayload` JSON fixture as a dict."""
    import json

    path = Path(__file__).parent / "fixtures" / "hydration_payload_sample.json"
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# Compose stack lifecycle (e2e)
# ---------------------------------------------------------------------------


def _backend_healthy(url: str) -> bool:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{url}/healthz", timeout=2) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError):
        return False


@pytest.fixture(scope="session")
def compose_stack(backend_url: str) -> None:
    """Assert the compose stack is up; skip if not.

    Does NOT bring it up — operator runs `make e2e-up`.
    """
    if not _backend_healthy(backend_url):
        pytest.skip(
            f"backend /healthz unreachable at {backend_url}; run `make e2e-up`"
        )


# ---------------------------------------------------------------------------
# HTTP / SSE clients
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_http_client(backend_url: str) -> AsyncIterator[Any]:
    """`httpx.AsyncClient` bound to the backend under test (over network)."""
    import httpx

    async with httpx.AsyncClient(
        base_url=backend_url,
        timeout=httpx.Timeout(420.0, connect=10.0),
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture
def sse_helpers():
    """Expose the AG-UI SSE helpers module."""
    from tests.fixtures import sse_client as _sse  # type: ignore[import-not-found]

    return _sse


# ---------------------------------------------------------------------------
# Database (real today — dev-1 shipped Track B)
# ---------------------------------------------------------------------------


def _test_database_url() -> str:
    return (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql+asyncpg://kloc:changeme@localhost:5432/kloc_agent"
    )


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[Any]:
    """Async SQLAlchemy session bound to the test DB.

    Skips the test if Postgres is unreachable so unit tests can still
    run in CI without a DB.
    """
    from sqlalchemy.exc import InterfaceError, OperationalError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = _test_database_url()
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.connect() as conn:
            from sqlalchemy import text as _text

            await conn.execute(_text("SELECT 1"))
    except (OperationalError, InterfaceError, OSError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres unreachable at {url}: {exc}")
    except ValueError as exc:
        # SQLAlchemy raises ValueError from _not_implemented() when
        # greenlet is missing — surface this as a skip with a clear
        # remediation rather than letting it look like a test bug.
        await engine.dispose()
        if "greenlet" in str(exc).lower():
            pytest.skip(
                f"sqlalchemy async needs greenlet; add `greenlet>=3.0` "
                f"to pyproject.toml dependencies (see QA report on this): {exc}"
            )
        raise

    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def truncate_all_tables(db_session) -> AsyncIterator[None]:
    """TRUNCATE the 4 tables before AND after each test that requests it."""
    from sqlalchemy import text as _text

    async def _wipe() -> None:
        await db_session.execute(
            _text(
                "TRUNCATE TABLE audit_log, artifact_metadata, messages, "
                "sessions RESTART IDENTITY CASCADE"
            )
        )
        await db_session.commit()

    await _wipe()
    try:
        yield
    finally:
        await _wipe()


# ---------------------------------------------------------------------------
# Backend app for in-process integration tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_in_process() -> AsyncIterator[Any]:
    """Yield the FastAPI app with lifespan started.

    Used by integration tests via httpx `ASGITransport`. Lifespan brings
    up engine + S3 client + RunnerRegistry stub; tests that need S3 must
    have MinIO reachable.

    Skips the test cleanly when:
    - `greenlet` is missing (surfaces as ValueError from SQLAlchemy
      `_not_implemented`)
    - Postgres / MinIO is unreachable (OperationalError / EndpointConnectionError)
    - The PGMQ extension is not installed on the Postgres instance
      (lifespan step 1a `ensure_extension` raises DBAPIError)
    """
    from src.main import create_app

    app = create_app()
    try:
        async with _LifespanManager(app) as app_ctx:
            yield app_ctx
    except ValueError as exc:
        if "greenlet" in str(exc).lower():
            pytest.skip(
                "sqlalchemy async needs greenlet; add `greenlet>=3.0` to "
                "pyproject.toml dependencies"
            )
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if any(
            tok in msg
            for tok in (
                "connection refused",
                "could not connect",
                "name or service not known",
                "nodename nor servname provided",
                "endpointconnectionerror",
                "pgmq",
                "extension",
            )
        ):
            pytest.skip(f"backend dependency unreachable: {exc}")
        raise


class _LifespanManager:
    """Minimal lifespan driver."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._lifespan_cm = None

    async def __aenter__(self) -> Any:
        self._lifespan_cm = self._app.router.lifespan_context(self._app)
        await self._lifespan_cm.__aenter__()
        return self._app

    async def __aexit__(self, exc_type, exc, tb) -> None:
        assert self._lifespan_cm is not None
        await self._lifespan_cm.__aexit__(exc_type, exc, tb)


@pytest_asyncio.fixture
async def asgi_client(app_in_process) -> AsyncIterator[Any]:
    """httpx.AsyncClient bound to the in-process FastAPI app."""
    import httpx

    transport = httpx.ASGITransport(app=app_in_process)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        yield client


class _RegisteredRunner:
    """Test handle to a stub entry installed on `RunnerRegistry`.

    Carries the `runner_id` and per-runner `secret` the webhook receiver
    will resolve via `RunnerRegistry.get_by_runner_id`; the receiver's
    strict mode rejects HMAC requests for unknown runner_ids, so e2e
    webhook tests must pre-install an entry instead of signing with the
    global bootstrap secret.
    """

    def __init__(self, runner_id: str, secret: str, session_id: str) -> None:
        self.runner_id = runner_id
        self.secret = secret
        self.session_id = session_id


@pytest_asyncio.fixture
async def registered_runner(app_in_process) -> AsyncIterator[_RegisteredRunner]:
    """Install a stub RegistryEntry that the HMAC webhook receiver can
    resolve via `RunnerRegistry.get_by_runner_id`.

    Webhook tests run against the production strict-mode auth path: the
    receiver looks up `runner_id` in the registry and HMAC-verifies with
    the per-runner secret. Without a registered entry the receiver
    refuses with 401 "unknown runner" before HMAC verification runs.

    The stub bypasses `_install_entry` (which would start warm-idle and
    heartbeat watchers) — we only need `entry.handle.runner_secret` for
    the secret-resolution path and the runner_id -> session_id reverse
    index. Cleanup pops the dicts before lifespan shutdown so
    `shutdown_all` does not see a watcher-less entry.
    """
    from types import SimpleNamespace

    runner_id = f"test-runner-{uuid.uuid4().hex[:8]}"
    secret = f"test-secret-{uuid.uuid4().hex}"
    session_id = str(uuid.uuid4())

    registry = app_in_process.state.runner_registry
    handle = SimpleNamespace(
        session_id=session_id,
        runner_id=runner_id,
        runner_secret=secret,
        container_id=f"test-container-{runner_id}",
    )
    entry = SimpleNamespace(
        handle=handle,
        in_flight_tool_calls={},
    )

    async with registry._lock:
        registry._by_runner_id[runner_id] = session_id
        registry._entries[session_id] = entry

    try:
        yield _RegisteredRunner(
            runner_id=runner_id, secret=secret, session_id=session_id
        )
    finally:
        async with registry._lock:
            registry._by_runner_id.pop(runner_id, None)
            registry._entries.pop(session_id, None)


# ---------------------------------------------------------------------------
# Runner Protocol fake (real today — dev-2 shipped the Protocol)
# ---------------------------------------------------------------------------


class FakeRunnerHandle:
    """Observable handle matching the `RunnerHandle` Protocol."""

    def __init__(self, session_id: str, runner_id: str | None = None) -> None:
        self.session_id = session_id
        self.runner_id = runner_id or str(uuid.uuid4())
        self.run_id = str(uuid.uuid4())
        self.runner_secret = "fake-secret"
        self.container_id = f"fake-container-{self.runner_id[:8]}"
        self._dead = False


class FakeRunner:
    """In-memory Runner Protocol impl. Records every call for assertion."""

    def __init__(
        self,
        outbound_events: list[dict] | None = None,
    ) -> None:
        self.spawn_calls: list[Any] = []
        self.send_calls: list[tuple[Any, dict]] = []
        self.terminate_calls: list[Any] = []
        self.is_alive_calls: list[Any] = []
        self.outbound_events: list[dict] = outbound_events or []
        self._inbound: dict[str, asyncio.Queue] = {}

    async def spawn(self, payload: Any) -> FakeRunnerHandle:
        self.spawn_calls.append(payload)
        session_id = getattr(payload, "session_id", None)
        if session_id is None and isinstance(payload, dict):
            session_id = payload.get("session_id")
        handle = FakeRunnerHandle(session_id=str(session_id))
        self._inbound[handle.session_id] = asyncio.Queue()
        return handle

    async def send_user_message(
        self, handle: FakeRunnerHandle, message: dict
    ) -> None:
        self.send_calls.append((handle, message))
        q = self._inbound.get(handle.session_id)
        if q is not None:
            await q.put(message)

    async def stream_events(self, handle: FakeRunnerHandle):
        for ev in self.outbound_events:
            yield ev

    async def terminate(self, handle: FakeRunnerHandle) -> None:
        self.terminate_calls.append(handle)
        handle._dead = True

    async def is_alive(self, handle: FakeRunnerHandle) -> bool:
        self.is_alive_calls.append(handle)
        return not handle._dead


@pytest.fixture
def mock_runner() -> FakeRunner:
    """Observable FakeRunner implementing the Runner Protocol."""
    return FakeRunner()


# ---------------------------------------------------------------------------
# Test doubles for runner/agent_factory unit tests (still stubs)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_model_provider():  # pragma: no cover - awaits Track G fixture lift
    pytest.skip("Awaits implementation — Track G fixture lift")


@pytest.fixture
def mock_tools():  # pragma: no cover - awaits Track G fixture lift
    pytest.skip("Awaits implementation — Track G fixture lift")


# ---------------------------------------------------------------------------
# Docker helpers (for e2e)
# ---------------------------------------------------------------------------


def _docker_ps_for_session(session_id: str) -> list[str]:
    """Return container IDs labelled with `kloc.session_id=<session_id>`."""
    try:
        out = subprocess.check_output(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=kloc.session_id={session_id}",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return []
    return [line for line in out.decode().splitlines() if line]


@pytest.fixture
def docker_ps_for_session():
    return _docker_ps_for_session


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------


def pytest_configure(config: Any) -> None:
    """Register custom markers in case pyproject.toml lookup is bypassed."""
    config.addinivalue_line("markers", "unit: pure Python, no IO, no network")
    config.addinivalue_line(
        "markers",
        "integration: real Postgres + backend HTTP; runner + LLM mocked",
    )
    config.addinivalue_line(
        "markers",
        "e2e: full compose + real Anthropic + real Docker runner",
    )
    config.addinivalue_line("markers", "slow: takes > 30s; usually e2e")
