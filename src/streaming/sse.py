"""Wrapper around `ag_ui.encoder.EventEncoder` and the FastAPI
`StreamingResponse` used by `src/api/stream.py`. Centralises SSE framing
so the route file stays a thin orchestrator.

Events arrive on the in-proc bus as plain `dict` payloads (cheap to
construct from the runner's JSONL frames). `EventEncoder.encode()`
requires a Pydantic `BaseEvent` — we coerce dict → typed event via the
`ag_ui.core.events.Event` discriminated union at this boundary (B-INFRA-SSE).
"""

from __future__ import annotations

from typing import AsyncIterator, Union

from fastapi import Request
from fastapi.responses import StreamingResponse

try:
    from ag_ui.encoder import EventEncoder  # type: ignore
    from ag_ui.core.events import BaseEvent, Event  # type: ignore
    from pydantic import TypeAdapter  # type: ignore

    _EVENT_ADAPTER: TypeAdapter = TypeAdapter(Event)
except ImportError:
    EventEncoder = None  # type: ignore
    BaseEvent = None  # type: ignore
    _EVENT_ADAPTER = None  # type: ignore


KEEPALIVE_LINE = ": keepalive\n\n"


def make_response(
    request: Request,
    generator: AsyncIterator[Union[dict, "BaseEvent"]],
) -> StreamingResponse:
    if EventEncoder is None or _EVENT_ADAPTER is None:
        raise RuntimeError(
            "ag-ui-protocol not installed; dev-1's pyproject.toml must "
            "include ag-ui-protocol==0.1.18 before SSE can run"
        )
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def stream() -> AsyncIterator[bytes]:
        async for event in generator:
            # Bus delivers plain dicts; AG-UI's encoder requires a typed
            # BaseEvent. Discriminated-union validation by `type` field.
            if isinstance(event, dict):
                event = _EVENT_ADAPTER.validate_python(event)
            yield encoder.encode(event).encode("utf-8")

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
