"""Runner Protocol and HydrationPayload type re-export. Plan task D3.

dev-1 owns the canonical `HydrationPayload` / `McpStdioEndpoint`
Pydantic schemas in `src/db/models.py` (Contract D, plan §501-§542).
This module re-exports them for runner-side imports so the protocol
contract is one symbol regardless of physical location.

The runtime fallback (plain `object`) is intentional: it keeps this
module importable while dev-1 ships `src/db/models.py` in parallel.
Once the real schema lands, the TYPE_CHECKING block becomes the
ground truth and mypy resolves to the real type."""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Once dev-1 ships src/db/models.py:HydrationPayload, switch this
    # to: from src.db.models import HydrationPayload, McpStdioEndpoint
    HydrationPayload = object
    McpStdioEndpoint = object
else:
    try:
        from src.db.models import HydrationPayload, McpStdioEndpoint  # noqa: F401
    except ImportError:
        HydrationPayload = object  # placeholder until dev-1 ships
        McpStdioEndpoint = object


@runtime_checkable
class RunnerHandle(Protocol):
    """Opaque per-spawn handle. Concrete impls (DockerRunner) attach
    their own fields: `runner_id`, `run_id`, `runner_secret`,
    `container_id`, `_dead`. Treat as opaque outside the impl module."""

    session_id: str


@runtime_checkable
class Runner(Protocol):
    """Seam for test fakes vs. real DockerRunner. The 5 methods quoted
    in architecture.md §3.3 line 188."""

    async def spawn(self, payload: "HydrationPayload") -> "RunnerHandle": ...

    async def send_user_message(
        self, handle: "RunnerHandle", message: dict
    ) -> None: ...

    def stream_events(
        self, handle: "RunnerHandle"
    ) -> AsyncIterator[dict]: ...

    async def terminate(self, handle: "RunnerHandle") -> None: ...

    async def is_alive(self, handle: "RunnerHandle") -> bool: ...
