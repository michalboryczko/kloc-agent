"""Wrapper around `ag_ui.encoder.EventEncoder` and FastAPI's
`StreamingResponse`. Centralises SSE framing so the route stays a thin
orchestrator.

Events arrive on the in-proc bus as plain dicts (cheap to construct
from the runner's JSONL frames). `EventEncoder.encode()` requires a
Pydantic `BaseEvent`, so we coerce dict → typed event via the
`ag_ui.core.events.Event` discriminated union at this boundary.
"""

from __future__ import annotations

import logging
from typing import AsyncIterator, Union

from fastapi import Request
from fastapi.responses import StreamingResponse

try:
    from ag_ui.encoder import EventEncoder  # type: ignore
    from ag_ui.core.events import BaseEvent, Event  # type: ignore
    from pydantic import TypeAdapter, ValidationError  # type: ignore

    _EVENT_ADAPTER: TypeAdapter = TypeAdapter(Event)
except ImportError:
    EventEncoder = None  # type: ignore
    BaseEvent = None  # type: ignore
    _EVENT_ADAPTER = None  # type: ignore
    ValidationError = Exception  # type: ignore


log = logging.getLogger(__name__)

KEEPALIVE_LINE = ": keepalive\n\n"


def make_response(
    request: Request,
    generator: AsyncIterator[Union[dict, "BaseEvent"]],
) -> StreamingResponse:
    if EventEncoder is None or _EVENT_ADAPTER is None:
        raise RuntimeError(
            "ag-ui-protocol not installed; pyproject.toml must include "
            "ag-ui-protocol==0.1.18 before SSE can run"
        )
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def stream() -> AsyncIterator[bytes]:
        async for event in generator:
            seq: int | None = None
            if isinstance(event, dict):
                raw_seq = event.get("seq")
                if isinstance(raw_seq, int):
                    seq = raw_seq
                # Bus delivers plain dicts; AG-UI's encoder requires a typed
                # BaseEvent. Validate-or-skip — an unknown tag (`runner_ready`,
                # `heartbeat`, future runner-internal frames that aren't part
                # of the AG-UI public protocol) used to crash the whole
                # StreamingResponse before a single byte was yielded. Now they
                # are logged and dropped so well-formed AG-UI frames still get
                # through to the client.
                try:
                    event = _EVENT_ADAPTER.validate_python(event)
                except ValidationError:
                    log.warning(
                        "sse.skip_unknown_event type=%r",
                        event.get("type") if isinstance(event, dict) else None,
                    )
                    continue
            else:
                raw_seq = getattr(event, "seq", None)
                if isinstance(raw_seq, int):
                    seq = raw_seq
            encoded = encoder.encode(event)
            if seq is not None:
                encoded = f"id: {seq}\n" + encoded
            yield encoded.encode("utf-8")

    return StreamingResponse(stream(), media_type=encoder.get_content_type())
