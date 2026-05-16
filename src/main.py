"""FastAPI application entrypoint.

Lifespan order:
  1. Create DB engine (sqlalchemy async)
  2. Create S3 client (aioboto3) on `app.state.s3`
  3. Initialize `RunnerRegistry` + `DockerRunner`
  4. Run boot-time orphan-container sweep
  5. Run boot-time DB orphan-message scan ('stream_orphaned')
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator

import aioboto3
from botocore.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api import artifacts, health, internal, sessions, stop, stream, webhooks
from src.db.engine import create_engine_for_settings, get_sessionmaker
from src.hooks_audit.emit import make_audit_emit_closure
from src.runner_mgmt import RunnerRegistry
from src.settings import get_settings
from src.streaming.event_bus import event_bus


# uvicorn keeps the root logger at WARNING; force the `kloc_agent`
# tree to INFO so operational log lines reach stdout.
logging.getLogger("kloc_agent").setLevel(logging.INFO)
logger = logging.getLogger("kloc_agent")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    stack = AsyncExitStack()

    # 1. DB engine
    engine = create_engine_for_settings(settings)
    app.state.engine = engine

    # 2. S3 client (lifespan-managed; research/04 §6.4)
    session = aioboto3.Session(
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name="us-east-1",
    )
    s3 = await stack.enter_async_context(
        session.client(
            "s3",
            endpoint_url=settings.minio_endpoint_url,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
            use_ssl=settings.minio_use_ssl,
        )
    )
    app.state.s3 = s3

    # 3. RunnerRegistry. Wire the real DockerRunner before the sweeper
    #    runs so the registry's `known_runner_ids()` (empty at boot) is
    #    available.
    app.state.settings = settings
    app.state.event_bus = event_bus
    # AG-UI 0.1.18 places `runId` only on lifecycle frames
    # (RUN_STARTED / RUN_FINISHED / RUN_ERROR); intermediate frames
    # correlate via messageId/toolCallId. `_dispatch_frame` caches the
    # active run_id per session here on RUN_STARTED so it can route
    # every non-lifecycle frame to the right bus topic. Process-local
    # dict: single uvicorn worker only.
    app.state.active_run_by_session = {}
    # Resume / replay buffer: under reconnect a non-lifecycle frame
    # may arrive before the new run's RUN_STARTED has been observed.
    # `_dispatch_frame` parks them per-session here and flushes when
    # the matching RUN_STARTED lands. Bounded; see _PRE_RUN_BUFFER_CAP.
    app.state.pending_pre_run_started = {}
    # audit_emit closure: each emit opens a fresh AsyncSession + commits
    # one row, so RunnerRegistry / WarmIdleManager / HeartbeatWatcher can
    # write audit rows without sharing the request-scoped session.
    audit_emit = make_audit_emit_closure(get_sessionmaker())
    runner_registry = RunnerRegistry(
        warm_idle_s=settings.runner_warm_idle_s,
        heartbeat_timeout_s=settings.runner_heartbeat_timeout_s,
        audit_emit=audit_emit,
    )
    # DockerRunner is the only runner mode. Construction failures
    # (ImportError when aiodocker is missing, daemon unreachable, etc.)
    # propagate to lifespan startup and abort boot. Tests inject a fake
    # Runner via `RunnerRegistry.set_runner()` instead of swapping to a
    # different runner mode.
    from src.runner_mgmt.docker_runner import DockerRunner

    docker_runner = DockerRunner(
        image=settings.runner_image_tag,
        network=settings.kloc_docker_network,
        backend_url=settings.backend_url,
        skills_host_dir=settings.kloc_skills_dir_host,
        event_bus=event_bus,
    )
    runner_registry.set_runner(docker_runner)
    app.state.runner_registry = runner_registry

    # 4. Boot-time orphan-container sweep. At boot the registry is
    #    empty, so any container labelled `kloc.role=runner` from a
    #    prior backend process is an orphan to be killed.
    try:
        from src.runner_mgmt import sweeper  # type: ignore[attr-defined]

        await sweeper.orphan_sweep(
            settings, known_runner_ids=runner_registry.known_runner_ids()
        )
    except (ImportError, AttributeError):
        logger.info("boot: orphan_sweep not available — skipping")
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("boot orphan-container sweep failed: %s", e)

    # 5. Boot-time DB orphan-message scan. Catch ONLY transient
    #    operational errors (DB unreachable, network blip). Misconfig
    #    bugs (missing greenlet, import errors, schema mismatches) MUST
    #    crash boot rather than be masked.
    from sqlalchemy.exc import OperationalError, InterfaceError, DBAPIError

    from src.repos.boot import sweep_orphaned_messages

    try:
        async with engine.begin() as conn:
            await sweep_orphaned_messages(conn)
    except (OperationalError, InterfaceError, DBAPIError) as e:
        logger.exception("boot orphan-message scan: DB unreachable (%s)", e)

    try:
        yield
    finally:
        # Shutdown order: stop runners first, then close DB + S3.
        try:
            await runner_registry.shutdown_all()
        except Exception as e:  # pragma: no cover - defensive
            logger.exception("runner_registry shutdown failed: %s", e)

        await engine.dispose()
        await stack.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="kloc-agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    # CORS: install BEFORE routers so browser preflight (OPTIONS) for any
    # mounted route is handled by Starlette's CORSMiddleware instead of
    # falling through to the router's 405. Allowed origins come from
    # `KLOC_CORS_ALLOW_ORIGINS` (comma-separated env var); default is the
    # Next.js dev server at http://localhost:3000.
    _settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id", "Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(sessions.router, prefix="/v1")
    app.include_router(webhooks.router, prefix="/v1")
    app.include_router(artifacts.router, prefix="/v1")
    app.include_router(stop.router, prefix="/v1")
    app.include_router(internal.router, prefix="/internal")
    app.include_router(stream.router, prefix="/v1")
    return app


app = create_app()
