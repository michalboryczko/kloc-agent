"""Boot-time lifespan contract tests for `src/main.py`.

Pins the behaviour: `DockerRunner` construction failures (missing
`aiodocker`, daemon unreachable, etc.) must propagate out of the
lifespan and abort app startup. A future refactor that wraps the
construction in a permissive `except Exception` would silently start
the app with a broken runner backend; this test catches that drift.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI


pytestmark = pytest.mark.unit


class _NoopEngine:
    def begin(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            async def execute(self_inner, *_args, **_kwargs):
                return None

        return _Ctx()

    async def dispose(self) -> None:
        return None


class _NoopS3Session:
    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs

    def client(self, *args: Any, **kwargs: Any) -> Any:
        class _ClientCtx:
            async def __aenter__(self_inner):
                return object()

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _ClientCtx()


async def _noop_sweep(_conn: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_lifespan_aborts_when_docker_runner_construction_fails(
    monkeypatch, tmp_path
) -> None:
    """When `DockerRunner.__init__` raises (e.g. `aiodocker` missing),
    the lifespan context-manager must surface the error rather than
    swallowing it and starting the app with a broken runner backend."""
    # Settings must construct cleanly for the lifespan to even begin.
    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.chdir(tmp_path)

    from src import main as main_mod
    from src.settings import get_settings
    import src.runner_mgmt.docker_runner as docker_runner_mod

    # Clear the settings cache so the env vars above take effect.
    get_settings.cache_clear()

    # Force DockerRunner.__init__ to raise via the documented failure
    # path: aiodocker missing at import time.
    monkeypatch.setattr(docker_runner_mod, "aiodocker", None)

    # Replace heavyweight boot dependencies with no-ops so the failure
    # we observe is unambiguously the DockerRunner construction step.
    monkeypatch.setattr(
        main_mod, "create_engine_for_settings", lambda _s: _NoopEngine()
    )
    monkeypatch.setattr(main_mod.aioboto3, "Session", _NoopS3Session)
    monkeypatch.setattr(
        "src.repos.boot.sweep_orphaned_messages", _noop_sweep
    )

    app = FastAPI()
    with pytest.raises(RuntimeError, match="aiodocker"):
        async with main_mod.lifespan(app):
            pass  # pragma: no cover - lifespan must raise before reaching here

    get_settings.cache_clear()
