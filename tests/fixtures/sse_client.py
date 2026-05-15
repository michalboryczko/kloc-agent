"""Async SSE parser for AG-UI 0.1.18 wire format.

Spec reference: `docs/research/02-backend-agui.md` §1 — every event is
emitted as a single SSE frame `data: {json}\\n\\n`. Shape lifted from
repo 2's `tests/integration/e2e/sse_client.py` (chatbot-app/agentcore),
generalised to AG-UI 33-event taxonomy.

Pure data-plane code: depends only on `httpx`. No `src/*` imports.
Safe to write today, before any backend code lands.

Usage:

```python
async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=120) as http:
    async with sse_get(http, "/v1/sessions/abc/stream") as events:
        async for event in events:
            print(event["type"], event)
```

Or via the higher-level helper:

```python
events: list[dict] = await collect_until(
    http, "POST", "/v1/sessions/abc/stream",
    json=run_agent_input,
    stop_when=lambda e: e["type"] == "RUN_FINISHED",
    timeout=120,
)
```
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable

import httpx


# AG-UI 0.1.18 — 33 event types; canonical list from research/02-backend-agui.md §2.
AGUI_EVENT_TYPES: frozenset[str] = frozenset(
    {
        # Run lifecycle (5)
        "RUN_STARTED",
        "RUN_FINISHED",
        "RUN_ERROR",
        "STEP_STARTED",
        "STEP_FINISHED",
        # Text message streaming (4)
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "TEXT_MESSAGE_CHUNK",
        # Tool call streaming (5)
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_CHUNK",
        # State (3)
        "STATE_SNAPSHOT",
        "STATE_DELTA",
        "MESSAGES_SNAPSHOT",
        # Activity (2)
        "ACTIVITY_SNAPSHOT",
        "ACTIVITY_DELTA",
        # Reasoning (7)
        "REASONING_START",
        "REASONING_MESSAGE_START",
        "REASONING_MESSAGE_CONTENT",
        "REASONING_MESSAGE_END",
        "REASONING_MESSAGE_CHUNK",
        "REASONING_END",
        "REASONING_ENCRYPTED_VALUE",
        # Thinking (3, lightweight; legacy compatibility)
        "THINKING_START",
        "THINKING_END",
        "THINKING_TEXT_MESSAGE_START",
        "THINKING_TEXT_MESSAGE_CONTENT",
        "THINKING_TEXT_MESSAGE_END",
        # Escape hatches (2)
        "RAW",
        "CUSTOM",
    }
)


class SSEParseError(ValueError):
    """Raised when an SSE frame cannot be parsed as a JSON AG-UI event."""


def parse_sse_frame(raw_frame: str) -> dict[str, Any]:
    """Parse one SSE frame body (no trailing \\n\\n) into an AG-UI event dict.

    A frame in our wire format is:

        data: {"type": "TEXT_MESSAGE_CONTENT", "messageId": "...", "delta": "..."}

    SSE allows multi-line `data:` fields (joined by \\n) and other field
    types like `event:`, `id:`, `retry:`. AG-UI uses single-line `data:`
    exclusively per `02-backend-agui.md` §1, but this parser is tolerant
    of `id:` (for `Last-Event-ID`-style resume cursors).
    """
    data_lines: list[str] = []
    event_id: str | None = None
    for line in raw_frame.splitlines():
        if not line or line.startswith(":"):
            continue
        if ":" not in line:
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "data":
            data_lines.append(value)
        elif field == "id":
            event_id = value
        # `event:` and `retry:` are ignored — AG-UI doesn't use them.
    if not data_lines:
        raise SSEParseError(f"frame has no data field: {raw_frame!r}")
    payload = "\n".join(data_lines)
    try:
        event: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SSEParseError(f"data field not valid JSON: {payload!r}") from exc
    if not isinstance(event, dict):
        raise SSEParseError(f"data field is not a JSON object: {payload!r}")
    if event_id is not None:
        # Surface the SSE id in a non-colliding key. AG-UI events have
        # their own optional `timestamp`/`runId` fields.
        event.setdefault("_sse_id", event_id)
    return event


async def iter_sse_events(
    response: httpx.Response,
) -> AsyncIterator[dict[str, Any]]:
    """Async-iterate AG-UI events from an open `httpx` streaming response.

    Frames are `\\n\\n`-separated; we accumulate bytes across chunks
    until we see a complete frame. Tolerates partial chunks landing on
    arbitrary byte boundaries (the common case).
    """
    buf = ""
    async for chunk in response.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            raw_frame, _, buf = buf.partition("\n\n")
            raw_frame = raw_frame.strip("\r")
            if not raw_frame:
                continue
            try:
                yield parse_sse_frame(raw_frame)
            except SSEParseError:
                # Keep going — one malformed frame should not kill the stream.
                # Tests assert on the events they care about; flaky middleware
                # injection (e.g. `: heartbeat` comments) is acceptable.
                continue


@asynccontextmanager
async def sse_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: Any | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float | httpx.Timeout = 120.0,
) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
    """Open an SSE stream and yield an async-iterator of parsed events.

    Sets `Accept: text/event-stream`; consumer is responsible for asserting
    a 200 status before iterating. Closes the connection on exit.
    """
    merged_headers = {"Accept": "text/event-stream"}
    if headers:
        merged_headers.update(headers)
    async with client.stream(
        method, url, json=json, headers=merged_headers, params=params, timeout=timeout
    ) as response:
        response.raise_for_status()
        yield iter_sse_events(response)


async def collect_until(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json: Any | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    stop_when: Callable[[dict[str, Any]], bool] | None = None,
    stop_types: Iterable[str] = ("RUN_FINISHED", "RUN_ERROR"),
    max_events: int = 10_000,
    timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """Consume an SSE stream into a list, stopping on a predicate.

    Default stop condition is `RUN_FINISHED` or `RUN_ERROR`. Used by
    most e2e scenario tests: send a `RunAgentInput`, get back the full
    event log for assertion.

    `max_events` is a safety net so a runaway stream doesn't OOM the
    test process. Raises `RuntimeError` if reached.
    """
    stop_types_set = frozenset(stop_types)

    def _default_stop(ev: dict[str, Any]) -> bool:
        return ev.get("type") in stop_types_set

    predicate = stop_when or _default_stop
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + timeout
    async with sse_request(
        client, method, url, json=json, headers=headers, params=params, timeout=timeout
    ) as stream:
        async for event in stream:
            events.append(event)
            if predicate(event):
                return events
            if len(events) >= max_events:
                raise RuntimeError(
                    f"collect_until exceeded max_events={max_events} without "
                    f"matching stop predicate; last event type="
                    f"{event.get('type')!r}"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"collect_until exceeded timeout={timeout}s; "
                    f"collected {len(events)} events"
                )
    return events


# ---------------------------------------------------------------------------
# Filtering helpers used by scenario assertions
# ---------------------------------------------------------------------------


def events_of_type(
    events: Iterable[dict[str, Any]], *types: str
) -> list[dict[str, Any]]:
    """Return events whose `type` field matches any of the given types."""
    wanted = frozenset(types)
    return [ev for ev in events if ev.get("type") in wanted]


def first_of_type(
    events: Iterable[dict[str, Any]], event_type: str
) -> dict[str, Any] | None:
    """Return the first event of a given type, or None."""
    for ev in events:
        if ev.get("type") == event_type:
            return ev
    return None


def assert_run_completed(events: list[dict[str, Any]]) -> None:
    """Assert the stream ended with `RUN_FINISHED` (not `RUN_ERROR`).

    Raises AssertionError with a useful message including the last 3
    event types when the assertion fails.
    """
    if not events:
        raise AssertionError("no events collected from SSE stream")
    last = events[-1]
    last_type = last.get("type")
    if last_type == "RUN_FINISHED":
        return
    tail = ", ".join(repr(ev.get("type")) for ev in events[-3:])
    raise AssertionError(
        f"stream did not end with RUN_FINISHED; last 3 types: [{tail}]; "
        f"last event: {last!r}"
    )


def total_bytes_by_type(events: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Sum `len(json.dumps(event))` per event type.

    Used by scenario 9 (`MESSAGES_SNAPSHOT` cardinality) to characterise
    the per-event-type byte volume across a 10-turn conversation.
    """
    out: dict[str, int] = {}
    for ev in events:
        t = ev.get("type", "UNKNOWN")
        # Match what would be on the wire: data + json payload + newlines.
        body = "data: " + json.dumps(ev, separators=(",", ":")) + "\n\n"
        out[t] = out.get(t, 0) + len(body.encode("utf-8"))
    return out
