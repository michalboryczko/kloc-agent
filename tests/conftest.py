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


@pytest.fixture(scope="session")
def llm_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip("GEMINI_API_KEY not set")
    return key


@pytest.fixture(scope="session")
def mcp_reachable() -> str:
    import urllib.error
    import urllib.request

    url = os.environ["KLOC_MCP_URL"]
    try:
        urllib.request.urlopen(url, timeout=2)
    except urllib.error.HTTPError:
        pass
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"MCP unreachable at {url}: {exc}")
    return url


@pytest.fixture(scope="session")
def backend_url() -> str:
    return os.environ["BACKEND_URL"]


@pytest.fixture(scope="session")
def hydration_payload_sample() -> dict[str, Any]:
    import json

    path = Path(__file__).parent / "fixtures" / "hydration_payload_sample.json"
    return json.loads(path.read_text())


@pytest.fixture(scope="session")
def compose_stack(backend_url: str) -> None:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{backend_url}/healthz", timeout=2) as resp:
            if 200 <= resp.status < 300:
                return
    except (urllib.error.URLError, OSError):
        pass
    pytest.skip(f"backend /healthz unreachable at {backend_url}")


@pytest_asyncio.fixture
async def async_http_client(backend_url: str) -> AsyncIterator[Any]:
    import httpx

    async with httpx.AsyncClient(
        base_url=backend_url,
        timeout=httpx.Timeout(420.0, connect=10.0),
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture
def sse_helpers():
    from tests.fixtures import sse_client as _sse  # type: ignore[import-not-found]

    return _sse


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[Any]:
    from sqlalchemy.exc import InterfaceError, OperationalError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with engine.connect() as conn:
            from sqlalchemy import text as _text

            await conn.execute(_text("SELECT 1"))
    except (OperationalError, InterfaceError, OSError) as exc:
        await engine.dispose()
        pytest.skip(f"Postgres unreachable at {url}: {exc}")

    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def truncate_all_tables(db_session) -> AsyncIterator[None]:
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


@pytest_asyncio.fixture
async def app_in_process() -> AsyncIterator[Any]:
    from src.main import create_app

    app = create_app()
    async with _LifespanManager(app) as app_ctx:
        yield app_ctx


class _LifespanManager:
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
    def __init__(self, runner_id: str, secret: str, session_id: str) -> None:
        self.runner_id = runner_id
        self.secret = secret
        self.session_id = session_id


@pytest_asyncio.fixture
async def registered_runner(app_in_process) -> AsyncIterator[_RegisteredRunner]:
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


class FakeRunnerHandle:
    def __init__(self, session_id: str, runner_id: str | None = None) -> None:
        self.session_id = session_id
        self.runner_id = runner_id or str(uuid.uuid4())
        self.run_id = str(uuid.uuid4())
        self.runner_secret = "fake-secret"
        self.container_id = f"fake-container-{self.runner_id[:8]}"
        self._dead = False


class FakeRunner:
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
    return FakeRunner()


@pytest.fixture
def mock_model_provider():
    pytest.skip("Awaits implementation")


@pytest.fixture
def mock_tools():
    pytest.skip("Awaits implementation")


def _docker_ps_for_session(session_id: str) -> list[str]:
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


def pytest_configure(config: Any) -> None:
    config.addinivalue_line("markers", "unit: pure Python, no IO, no network")
    config.addinivalue_line(
        "markers", "integration: real Postgres + backend HTTP"
    )
    config.addinivalue_line(
        "markers", "e2e: full compose + real Docker runner"
    )
    config.addinivalue_line("markers", "slow: takes > 30s")
