"""aiodocker-based runner. Plan task D5.

Lifecycle: pull -> create (with HostConfig: Mem 1 GiB, NanoCpus 2e9,
PidsLimit 256, RestartPolicy `no`, label `kloc.role=runner`, bridge
network `<project>_default`) -> start -> stop(t=5) -> wait -> delete.

The runner inside the container talks to the backend via HTTP only
(chunked POST out, long-poll GET in) -- see `runner/channel.py`. The
backend therefore does NOT attach to the container's stdio for app
traffic; it only consumes events from `src/api/internal.py`."""

from __future__ import annotations

import logging
import os
import secrets
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

try:
    import aiodocker  # type: ignore
except ImportError:
    aiodocker = None  # type: ignore

from .hydrate import (
    build_hydration_mount,
    build_skills_mount,
    cleanup_hydration_tempfile,
    runner_mount_path_for,
    write_hydration_tempfile,
)
from .protocol import Runner, RunnerHandle

if TYPE_CHECKING:
    from src.streaming.event_bus import EventBus

log = logging.getLogger(__name__)


@dataclass
class DockerRunnerHandle:
    session_id: str
    runner_id: str
    run_id: str
    runner_secret: str
    container: Any
    _dead: bool = field(default=False)


class DockerRunner(Runner):
    """Concrete `Runner` Protocol impl. One `DockerRunner` per backend
    process; spawns one container per session."""

    def __init__(
        self,
        image: str,
        network: str,
        backend_url: str,
        skills_host_dir: str,
        memory_bytes: int = 1 * 1024 * 1024 * 1024,
        nano_cpus: int = 2 * 1_000_000_000,
        pids_limit: int = 256,
        event_bus: "EventBus | None" = None,
    ) -> None:
        if aiodocker is None:
            raise RuntimeError(
                "aiodocker not installed; dev-1's pyproject.toml must "
                "include aiodocker>=0.26"
            )
        self._docker = aiodocker.Docker()
        self._image = image
        self._network = network
        self._backend_url = backend_url
        self._skills_host_dir = skills_host_dir
        self._memory_bytes = memory_bytes
        self._nano_cpus = nano_cpus
        self._pids_limit = pids_limit
        self._event_bus = event_bus

    async def close(self) -> None:
        try:
            await self._docker.close()
        except Exception:
            log.exception("docker_runner.close_failed")

    async def spawn(self, payload: Any) -> DockerRunnerHandle:
        session_id = getattr(payload, "session_id", None) or payload["session_id"]
        run_id = getattr(payload, "run_id", None) or payload.get("run_id") or str(
            uuid.uuid4()
        )
        runner_id = getattr(payload, "runner_id", None) or payload.get(
            "runner_id"
        ) or str(uuid.uuid4())
        runner_secret = (
            getattr(payload, "runner_secret", None)
            or payload.get("runner_secret")
            or secrets.token_urlsafe(32)
        )

        write_hydration_tempfile(runner_id, payload)
        # Per-spawn path inside the runner container — surfaces via the
        # shared `kloc-hydration` named volume. The legacy fixed path
        # `/run/kloc/hydration.json` is replaced by `/run/kloc/<rid>.json`
        # so multiple concurrent spawns can share the volume without
        # filename collisions (B-INFRA-3).
        runner_hydration_path = runner_mount_path_for(runner_id)
        env_block = [
            f"KLOC_SESSION_ID={session_id}",
            f"KLOC_RUNNER_ID={runner_id}",
            f"KLOC_RUN_ID={run_id}",
            f"KLOC_RUNNER_SECRET={runner_secret}",
            f"KLOC_BACKEND_URL={self._backend_url}",
            f"KLOC_HYDRATION_PATH={runner_hydration_path}",
        ]
        # Forward LLM provider credentials. Each provider in
        # `runner/model_factory.py:create_model` consumes its own env;
        # we forward all known keys unconditionally and the runner-side
        # branch picks the right one (constraint 4: explicit model).
        #   ANTHROPIC_BASE_URL — optional proxy / alternate region.
        #   GEMINI_API_KEY / GOOGLE_API_KEY — google-genai accepts
        #   either name; forwarding both is belt-and-braces against
        #   operator config drift.
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ):
            value = os.environ.get(key)
            if value:
                env_block.append(f"{key}={value}")
        # Forward OTEL_* from the backend process env so the runner's
        # `opentelemetry-instrument` ENTRYPOINT (Track H2, dev-3) ships
        # spans to the same collector as the backend. The image already
        # sets OTEL_SERVICE_NAME=kloc-agent-runner +
        # OTEL_RESOURCE_ATTRIBUTES; we do NOT override those unless the
        # operator explicitly sets a per-process override on the backend.
        for key, value in os.environ.items():
            if key.startswith("OTEL_") and key not in {
                "OTEL_SERVICE_NAME",
                "OTEL_RESOURCE_ATTRIBUTES",
            }:
                env_block.append(f"{key}={value}")
        config = {
            "Image": self._image,
            "Env": env_block,
            "HostConfig": {
                "Memory": self._memory_bytes,
                "MemorySwap": self._memory_bytes,
                "NanoCpus": self._nano_cpus,
                "PidsLimit": self._pids_limit,
                "RestartPolicy": {"Name": "no"},
                "NetworkMode": self._network,
                "AutoRemove": False,
                "Mounts": [
                    build_hydration_mount(),
                    build_skills_mount(self._skills_host_dir),
                ],
                # Resolve `host.docker.internal` to the host gateway so
                # the runner can reach the operator-managed
                # kloc-intelligence Streamable-HTTP MCP server running
                # outside the compose project (Contract D pivot). Docker
                # Desktop handles this implicitly; explicit ExtraHosts is
                # required on Linux Docker and harmless elsewhere.
                "ExtraHosts": ["host.docker.internal:host-gateway"],
            },
            "Labels": {
                "kloc.role": "runner",
                "kloc.session_id": session_id,
                "kloc.runner_id": runner_id,
            },
        }

        container = await self._docker.containers.create(
            config=config, name=f"kloc-runner-{runner_id}"
        )
        await container.start()
        log.info(
            "runner.spawned",
            extra={
                "session_id": session_id,
                "runner_id": runner_id,
                "container_id": container.id,
            },
        )
        return DockerRunnerHandle(
            session_id=session_id,
            runner_id=runner_id,
            run_id=run_id,
            runner_secret=runner_secret,
            container=container,
        )

    async def send_user_message(
        self, handle: RunnerHandle, message: dict
    ) -> None:
        # Backend pushes onto an in-process inbox queue that the runner
        # picks up via long-poll against `src/api/internal.py`. This
        # method is a thin shim; the actual queue is owned by
        # `RunnerRegistry`.
        raise NotImplementedError(
            "send_user_message is dispatched via RunnerRegistry.inbox"
        )

    async def stream_events(
        self, handle: RunnerHandle
    ) -> AsyncIterator[dict]:
        # Events arrive over HTTP at `/internal/sessions/{id}/events`
        # (Contract B) and are pushed onto the in-process EventBus by
        # dev-1's `src/api/internal.py`. SSE consumers subscribe via
        # the registry/bus directly; this method is unused in the HTTP
        # transport but kept for the Protocol.
        if self._event_bus is None:
            raise RuntimeError(
                "DockerRunner.stream_events requires an event_bus"
            )
        h = handle  # narrow
        async for event in self._event_bus.subscribe(h.session_id, h.run_id):  # type: ignore[attr-defined]
            yield event

    async def terminate(self, handle: RunnerHandle) -> None:
        h = handle  # narrow
        if h._dead:  # type: ignore[attr-defined]
            return
        h._dead = True  # type: ignore[attr-defined]
        try:
            await h.container.stop(t=5)  # type: ignore[attr-defined]
        except Exception:
            log.exception("runner.stop_failed")
        try:
            await h.container.wait()  # type: ignore[attr-defined]
        except Exception:
            log.exception("runner.wait_failed")
        try:
            await h.container.delete(force=True)  # type: ignore[attr-defined]
        except Exception:
            log.exception("runner.delete_failed")
        cleanup_hydration_tempfile(h.runner_id)  # type: ignore[attr-defined]
        log.info(
            "runner.terminated",
            extra={
                "session_id": h.session_id,  # type: ignore[attr-defined]
                "runner_id": h.runner_id,  # type: ignore[attr-defined]
            },
        )

    async def is_alive(self, handle: RunnerHandle) -> bool:
        h = handle  # narrow
        if h._dead:  # type: ignore[attr-defined]
            return False
        try:
            data = await h.container.show()  # type: ignore[attr-defined]
            return bool(data.get("State", {}).get("Running"))
        except Exception:
            return False
