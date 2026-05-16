"""Runner entrypoint.

  1. read hydration JSON from the path supplied via `KLOC_HYDRATION_PATH`
  2. open `MCPClient`(s) in a `with` scope (lifecycle == whole run)
  3. build the Strands agent via `agent_factory`
  4. wrap in `ag_ui_strands.StrandsAgent`
  5. enter the `iter_inbound()` long-poll loop
  6. on each user message, call `agui_agent.run(RunAgentInput)` and
     `await channel.emit(event)` per yielded AG-UI event.

The MCP `with` scope wraps the whole event loop so an MCPClient
subprocess death is equivalent to a runner exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .agent_factory import build_agent, wrap_for_agui
from .channel import BackendChannel
from .hooks.audit import AuditHookSender
from .mcp_clients import build_mcp_clients

log = logging.getLogger(__name__)


MAX_HYDRATION_BYTES = 4 * 1024 * 1024  # 4 MiB cap on hydration JSON.


def _read_hydration() -> dict:
    path = os.environ.get(
        "KLOC_HYDRATION_PATH", "/run/kloc/hydration.json"
    )
    size = os.path.getsize(path)
    if size > MAX_HYDRATION_BYTES:
        raise RuntimeError(
            f"hydration file {path} exceeds {MAX_HYDRATION_BYTES} bytes "
            f"(actual: {size}); refusing to load"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read(MAX_HYDRATION_BYTES))


async def _run() -> None:
    payload = _read_hydration()
    session_id = payload["session_id"]
    runner_id = payload["runner_id"]
    initial_run_id = payload["run_id"]
    runner_secret = payload["runner_secret"]
    backend_url = payload.get("backend_url") or os.environ.get(
        "KLOC_BACKEND_URL"
    )
    if not backend_url:
        raise RuntimeError("hydration missing backend_url")

    current_run_id = {"value": initial_run_id}

    channel = BackendChannel(
        backend_url=backend_url,
        session_id=session_id,
        runner_id=runner_id,
        run_id_provider=lambda: current_run_id["value"],
    )
    await channel.start()

    async def emit_custom(event: dict) -> None:
        await channel.emit(event)

    audit_sender = AuditHookSender(
        backend_url=backend_url,
        runner_id=runner_id,
        session_id=session_id,
        run_id_provider=lambda: current_run_id["value"],
        runner_secret=runner_secret,
        emit_custom_event=emit_custom,
    )
    await audit_sender.start()

    mcp_endpoints = payload.get("mcp_endpoints") or []
    mcp_clients = build_mcp_clients(mcp_endpoints)

    try:
        async with contextlib.AsyncExitStack() as stack:
            mcp_tools: list[Any] = []
            for client in mcp_clients:
                # Strands' MCPClient is a sync context manager (it spawns
                # an internal thread for the async MCP loop, exposed via
                # `list_tools_sync()`). AsyncExitStack handles both sync
                # and async clients via `enter_context` — using
                # AsyncExitStack rather than ExitStack future-proofs us
                # if Strands ships a true async MCP client.
                if hasattr(client, "__aenter__"):
                    await stack.enter_async_context(client)
                else:
                    stack.enter_context(client)
                mcp_tools.extend(client.list_tools_sync())

            strands_agent, audit_provider = build_agent(
                payload, mcp_tools, audit_sender
            )
            agui_agent = wrap_for_agui(strands_agent, audit_provider)

            await channel.emit(
                {
                    "type": "runner_ready",
                    "session_id": session_id,
                    "runner_id": runner_id,
                }
            )

            first_turn = True
            async for inbound in channel.iter_inbound():
                if inbound.get("type") != "user_message":
                    log.warning(
                        "runner.unexpected_inbound",
                        extra={"type": inbound.get("type")},
                    )
                    continue
                current_run_id["value"] = inbound.get("run_id") or str(
                    uuid.uuid4()
                )
                channel.set_busy(True)
                try:
                    await _run_one_turn(
                        agui_agent=agui_agent,
                        inbound=inbound,
                        channel=channel,
                        session_id=session_id,
                        run_id=current_run_id["value"],
                        payload=payload,
                        first_turn=first_turn,
                    )
                    first_turn = False
                finally:
                    channel.set_busy(False)
    finally:
        await audit_sender.stop()
        await channel.stop()


async def _run_one_turn(
    agui_agent: Any,
    inbound: dict,
    channel: BackendChannel,
    session_id: str,
    run_id: str,
    payload: dict,
    first_turn: bool,
) -> None:
    from ag_ui.core.types import RunAgentInput  # type: ignore

    # Architecture.md §3.4: ag_ui_strands.StrandsAgent rebuilds the
    # internal `messages` list from `RunAgentInput.messages` on every
    # call. The backend's `src/api/stream.py` always pushes the *new*
    # user turn(s) in `inbound["messages"]`; `prior_messages` carries
    # the full history loaded from Postgres at spawn time. Merge ONLY
    # on the first turn of a warm container; on subsequent turns the
    # backend has already persisted prior assistant replies + new
    # user turn and pushes the cumulative list itself, so prepending
    # `prior_messages` would duplicate turn 0.
    if first_turn:
        messages = list(payload.get("prior_messages") or [])
        messages.extend(inbound.get("messages") or [])
    else:
        messages = list(inbound.get("messages") or [])

    run_input = RunAgentInput(
        thread_id=session_id,
        run_id=run_id,
        messages=messages,
        tools=[],
        context=[],
        state=payload.get("state") or {},
        forwarded_props={},
    )

    try:
        async for event in agui_agent.run(run_input):
            wire = (
                event.model_dump(by_alias=True, exclude_none=True)
                if hasattr(event, "model_dump")
                else dict(event)
            )
            await channel.emit(wire)
    except Exception as exc:  # plan D18
        log.exception("runner.turn_failed")
        await channel.emit(
            {
                "type": "RUN_ERROR",
                "threadId": session_id,
                "runId": run_id,
                "code": "STRANDS_ERROR",
                "message": str(exc),
                "cause": "runner_crashed",
            }
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    # H4 — intentionally deferred per dev-3. `opentelemetry-instrument`
    # (runner/Dockerfile ENTRYPOINT) installs the OTel SDK tracer/meter
    # provider from OTEL_TRACES_EXPORTER env at boot;
    # `OTEL_TRACES_EXPORTER=otlp,console` (set via backend spawn env)
    # already registers the ConsoleSpanExporter. An explicit
    # `StrandsTelemetry().setup_console_exporter()` would duplicate
    # spans on stdout or no-op against the pre-installed provider.
    # Revisit only if Strands' internal tool-call spans don't surface
    # in auto-instrumentation traces (open question per
    # investigation.md §8).
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
