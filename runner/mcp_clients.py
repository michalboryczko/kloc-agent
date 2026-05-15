"""MCP client builder. Plan task D12 + Contract D pivot.

The runner accepts mixed `mcp_endpoints` from the HydrationPayload:
  * `McpStdioEndpoint` (`command`, `args`, `env`) — spawn an MCP child
    process via stdio. Used when the MCP server runs inside the runner
    container (legacy).
  * `McpHttpEndpoint` (`url`) — Streamable-HTTP transport (MCP
    2025-03-26 spec). Used to reach kloc-intelligence's
    `mcp-server-http` subcommand at `http://host.docker.internal:8765/mcp`
    or similar. The operator brings up kloc-intelligence's stack
    (Neo4j + Qdrant + the HTTP MCP server) out of band; the agent
    treats it as an opaque MCP endpoint."""

from __future__ import annotations

import contextlib
from typing import Any, Iterable


def build_mcp_clients(endpoints: Iterable[Any]) -> list[Any]:
    """Return un-entered `MCPClient` instances. Caller opens each with
    `with` (lifecycle == whole session, plan R11).

    Discriminates endpoint type by attribute (`url` -> HTTP, otherwise
    stdio). Late-binds capture via default args so the per-endpoint
    config isn't accidentally shared across loop iterations."""
    from mcp import StdioServerParameters, stdio_client  # type: ignore
    from mcp.client.streamable_http import streamablehttp_client  # type: ignore
    from strands.tools.mcp import MCPClient  # type: ignore

    clients: list[Any] = []
    for ep in endpoints:
        url = getattr(ep, "url", None)
        if url is None and isinstance(ep, dict):
            url = ep.get("url")
        if url:
            clients.append(MCPClient(_make_http_factory(url)))
        else:
            command = getattr(ep, "command", None) or ep["command"]
            args = list(getattr(ep, "args", None) or ep["args"])
            env_attr = getattr(ep, "env", None)
            env = dict(env_attr if env_attr is not None else ep.get("env", {}))
            clients.append(MCPClient(_make_stdio_factory(command, args, env)))
    return clients


def _make_stdio_factory(command: str, args: list[str], env: dict[str, str]):
    from mcp import StdioServerParameters, stdio_client  # type: ignore

    def _factory():
        return stdio_client(
            StdioServerParameters(command=command, args=args, env=env)
        )

    return _factory


def _make_http_factory(url: str):
    """Wrap `streamablehttp_client` so Strands' `MCPClient` sees a
    2-tuple `(read, write)` context manager.

    `streamablehttp_client(url)` returns an async context manager
    yielding `(read, write, get_session_id)` (3-tuple, per the MCP
    Python SDK Streamable-HTTP impl). Strands' `MCPClient` accepts a
    callable returning a 2-tuple context manager; we adapt with a thin
    `asynccontextmanager` that discards the session-id getter."""
    from mcp.client.streamable_http import streamablehttp_client  # type: ignore

    @contextlib.asynccontextmanager
    async def _two_tuple():
        async with streamablehttp_client(url) as triple:
            read, write, _get_session_id = triple
            yield (read, write)

    def _factory():
        return _two_tuple()

    return _factory
