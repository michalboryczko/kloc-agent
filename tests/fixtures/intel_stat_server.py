"""Fixture stat-server for the BeforeToolCall policy layer.

Stand-in for the kloc-intelligence `/v1/file_stat` endpoint. The real
endpoint lives in a sibling repo; this fixture is the canonical
contract source until that cross-repo change lands.

Two usage shapes:
- In-process ASGI: `make_stat_server({...})` returns a Starlette app
  the unit tests mount via `httpx.AsyncClient(transport=ASGITransport(app=...))`.
- Standalone: `python -m tests.fixtures.intel_stat_server` boots a
  uvicorn server on `INTEL_STAT_PORT` (default 8765) for the
  integration test against a real backend.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


PathStats = dict[str, dict[str, Any]]


class FixtureState:
    def __init__(self) -> None:
        self.stats: PathStats = {}
        self.delay_s: float = 0.0
        self.force_status: int | None = None
        self.request_count: int = 0
        self.last_path: str | None = None

    def set_stat(
        self, path: str, *, exists: bool, size_bytes: int, is_file: bool
    ) -> None:
        self.stats[path] = {
            "exists": exists,
            "size_bytes": size_bytes,
            "is_file": is_file,
        }


def make_stat_server(initial: PathStats | None = None) -> Starlette:
    state = FixtureState()
    if initial:
        state.stats = {k: dict(v) for k, v in initial.items()}

    async def file_stat(request: Request) -> Response:
        state.request_count += 1
        path = request.query_params.get("path", "")
        state.last_path = path
        if state.delay_s > 0:
            await asyncio.sleep(state.delay_s)
        if state.force_status is not None:
            return Response(
                content=b"forced failure",
                status_code=state.force_status,
                media_type="text/plain",
            )
        info = state.stats.get(path)
        if info is None:
            return JSONResponse(
                {"exists": False, "size_bytes": 0, "is_file": False},
                status_code=404,
            )
        return JSONResponse(info, status_code=200)

    app = Starlette(routes=[Route("/v1/file_stat", file_stat, methods=["GET"])])
    app.state.fixture = state
    return app


def main() -> None:  # pragma: no cover
    import uvicorn

    port = int(os.environ.get("INTEL_STAT_PORT", "8765"))
    initial_json = os.environ.get("INTEL_STAT_INITIAL")
    initial: PathStats = {}
    if initial_json:
        initial = json.loads(initial_json)
    app = make_stat_server(initial)
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":  # pragma: no cover
    main()
