"""Runner Protocol and HydrationPayload type re-export.

Re-exports `HydrationPayload` / `McpStdioEndpoint` from `src/db/models.py`
so runner-side imports see the contract from a single physical module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    HydrationPayload = object
    McpStdioEndpoint = object
else:
    try:
        from src.db.models import HydrationPayload, McpStdioEndpoint  # noqa: F401
    except ImportError:
        # Fallback keeps this module importable in test environments that
        # stub out src.db.models. mypy resolves to the real type.
        HydrationPayload = object
        McpStdioEndpoint = object


@runtime_checkable
class RunnerHandle(Protocol):
    """Opaque per-spawn handle. Concrete impls attach their own fields
    (`runner_id`, `run_id`, `runner_secret`, `container_id`, `_dead`);
    treat as opaque outside the impl module."""

    session_id: str


@runtime_checkable
class Runner(Protocol):
    """Seam for test fakes vs. real DockerRunner."""

    async def spawn(self, payload: "HydrationPayload") -> "RunnerHandle": ...

    async def send_user_message(
        self, handle: "RunnerHandle", message: dict
    ) -> None: ...

    def stream_events(
        self, handle: "RunnerHandle"
    ) -> AsyncIterator[dict]: ...

    async def terminate(self, handle: "RunnerHandle") -> None: ...

    async def is_alive(self, handle: "RunnerHandle") -> bool: ...
