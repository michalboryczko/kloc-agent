"""Backend↔Runner internal API.

POST /internal/sessions/{id}/events   chunked JSONL ingress (runner → backend)
GET  /internal/sessions/{id}/inbox    long-poll (backend → runner, ≤ 25 s)

Localhost-only inside the compose bridge; no public auth.

Owns the transport: parse JSONL frames off the wire and publish AG-UI
events into the in-proc `EventBus` keyed by `(session_id, run_id)`.
Lifecycle frames (`heartbeat`, `RunnerHeartbeat`) reset the runner
registry's heartbeat watcher; everything else flows to the bus.

AG-UI events carry `runId` (camelCase); runner-internal liveness frames
carry `type: "heartbeat"`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from src.settings import get_settings


router = APIRouter(tags=["internal"])
log = logging.getLogger("kloc_agent.internal")


def _diag(msg: str) -> None:
    """Off by default. Direct stderr bypasses uvicorn's --log-config
    which can filter `kloc_agent.*` INFO records in some images."""
    if not get_settings().diag_events:
        return
    print(msg, file=sys.stderr, flush=True)


async def _dispatch_frame(request: Request, session_id: str, frame: dict) -> None:
    """Route one JSONL frame to the right consumer.

    - `type: "heartbeat"` / `"RunnerHeartbeat"` →
      `RunnerRegistry.on_heartbeat_frame(session_id)`. Never published
      to the event bus — these are liveness signals, not AG-UI events.
    - `type: "RUN_FINISHED"` → also notify
      `RunnerRegistry.on_run_finished` so the warm-idle countdown
      starts; the frame is also published to the event bus so SSE
      consumers can close cleanly.
    - AG-UI event with `runId` → `event_bus.publish(...)`.
    - Anything else → drop without crashing the ingress.
    """
    frame_type = frame.get("type") or ""
    registry = getattr(request.app.state, "runner_registry", None)
    run_id = frame.get("runId") or frame.get("run_id")
    _diag(
        f"frame: type={frame_type} run_id={run_id} session_id={session_id}"
    )

    if frame_type in ("heartbeat", "RunnerHeartbeat"):
        on_hb = getattr(registry, "on_heartbeat_frame", None) if registry else None
        if on_hb is not None:
            try:
                await on_hb(session_id)
                _diag(f"dispatched: type={frame_type} -> on_heartbeat_frame")
            except Exception:  # pragma: no cover - defensive
                log.exception("on_heartbeat_frame failed")
        return

    if frame_type == "RUN_FINISHED":
        on_finished = getattr(registry, "on_run_finished", None) if registry else None
        if on_finished is not None:
            try:
                await on_finished(session_id)
                _diag("dispatched: type=RUN_FINISHED -> on_run_finished")
            except Exception:  # pragma: no cover - defensive
                log.exception("on_run_finished failed")
        # Fall through — RUN_FINISHED IS an AG-UI event; SSE consumers
        # need it to close the stream cleanly.

    bus = getattr(request.app.state, "event_bus", None)
    if bus is None:
        _diag(f"no bus: type={frame_type} dropped")
        return

    # Only RUN_STARTED / RUN_FINISHED / RUN_ERROR carry `runId`;
    # intermediate frames correlate via messageId/toolCallId. Cache the
    # active run per session on RUN_STARTED so intermediate frames key
    # onto the right bus topic; clear on RUN_FINISHED / RUN_ERROR.
    # Process-local dict — single uvicorn worker only.
    active_by_session: dict[str, str] | None = getattr(
        request.app.state, "active_run_by_session", None
    )
    # Pre-RUN_STARTED buffer: under resume / reconnect, replay frames
    # may arrive before the cached `RUN_STARTED` lands (or before the
    # backend re-establishes the run id). Holding them aside until we
    # see RUN_STARTED prevents a silent drop.
    pending_by_session: dict[str, list[dict]] | None = getattr(
        request.app.state, "pending_pre_run_started", None
    )
    if run_id:
        if frame_type == "RUN_STARTED" and active_by_session is not None:
            active_by_session[session_id] = str(run_id)
    elif active_by_session is not None:
        run_id = active_by_session.get(session_id)

    if not run_id:
        # Genuine orphan: a non-lifecycle frame arrived before any
        # RUN_STARTED was seen for this session. Buffer it instead of
        # silently dropping so resume / replay does not lose state.
        if pending_by_session is not None:
            buf = pending_by_session.setdefault(session_id, [])
            # Bound the buffer so a misbehaving runner cannot OOM us.
            if len(buf) < _PRE_RUN_BUFFER_CAP:
                buf.append(frame)
                _diag(
                    f"buffered (no active run): type={frame_type} "
                    f"session_id={session_id} buffered={len(buf)}"
                )
            else:
                _diag(
                    f"dropped (buffer full): type={frame_type} "
                    f"session_id={session_id} cap={_PRE_RUN_BUFFER_CAP}"
                )
        else:
            _diag(
                f"orphan (no active run): type={frame_type} "
                f"session_id={session_id} frame_keys={list(frame.keys())}"
            )
        return

    await bus.publish(session_id, str(run_id), frame)
    _diag(
        f"dispatched: type={frame_type} -> bus(sid={session_id}, "
        f"rid={run_id})"
    )

    # Flush any pre-RUN_STARTED orphan buffer AFTER the RUN_STARTED frame
    # itself reaches the bus, so subscribers observe RUN_STARTED at
    # index 0 — cursor-replay and resume depend on this ordering.
    if frame_type == "RUN_STARTED" and pending_by_session is not None:
        pending = pending_by_session.pop(session_id, None)
        if pending:
            for buf in pending:
                await bus.publish(session_id, str(run_id), buf)
            _diag(
                f"replay flush: session_id={session_id} "
                f"count={len(pending)} run_id={run_id}"
            )

    if (
        frame_type in ("RUN_FINISHED", "RUN_ERROR")
        and active_by_session is not None
    ):
        # End-of-run cleanup so a stale run_id cannot leak into the next
        # run on this session. Compare-and-swap: only pop when the
        # cached active run matches THIS terminal frame's run_id. A late
        # RUN_FINISHED(rA) racing a fresh RUN_STARTED(rB) on the same
        # session must NOT wipe B's mapping, or B's next intermediate
        # frame would be misrouted as a spurious orphan.
        if active_by_session.get(session_id) == str(run_id):
            active_by_session.pop(session_id, None)


# Per-line cap on JSONL frames. 1 MiB is generous for AG-UI events;
# anything larger is almost certainly a runaway or attack.
MAX_LINE_BYTES = 1 * 1024 * 1024

# Cap on frames buffered per-session before RUN_STARTED is observed.
# Bounded so a misbehaving runner that never emits RUN_STARTED cannot
# OOM the backend.
_PRE_RUN_BUFFER_CAP = 1024


@router.post(
    "/sessions/{session_id}/events",
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_runner_events(
    session_id: uuid.UUID,
    request: Request,
) -> JSONResponse:
    """Chunked JSONL ingress from runner.

    Reads `request.stream()` line-by-line; each line is one JSON object.
    Each frame is dispatched via `_dispatch_frame`. Lines larger than
    `MAX_LINE_BYTES` are rejected with 413 to bound memory.
    """
    sid = str(session_id)
    count = 0
    total_bytes = 0
    chunk_count = 0
    buf = b""
    _diag(f"rx open: session_id={sid}")
    try:
        async for chunk in request.stream():
            buf += chunk
            total_bytes += len(chunk)
            chunk_count += 1
            if chunk_count <= 3 or chunk_count % 10 == 0:
                # Sampled chunk-level diag (avoid log spam on long-lived
                # streams). First three chunks always logged; afterwards
                # every 10th. Helps confirm bytes are flowing even when no
                # newline appears for a while.
                _diag(
                    f"rx chunk: session_id={sid} chunk_n={chunk_count} "
                    f"chunk_bytes={len(chunk)} buf_bytes={len(buf)} "
                    f"total_bytes={total_bytes}"
                )
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                if len(line) > MAX_LINE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"JSONL frame exceeds {MAX_LINE_BYTES} bytes",
                    )
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError as e:
                    _diag(f"parse fail: line={line[:200]!r} err={e}")
                    raise HTTPException(
                        status_code=400, detail="invalid JSONL frame"
                    )
                count += 1
                await _dispatch_frame(request, sid, frame)
            if len(buf) > MAX_LINE_BYTES:
                # Pending line (no newline yet) already over cap — fail fast
                # so we don't accumulate bytes indefinitely.
                raise HTTPException(
                    status_code=413,
                    detail=f"JSONL frame exceeds {MAX_LINE_BYTES} bytes",
                )

        # Final flush — handle a trailing line without a newline.
        buf = buf.strip()
        if buf:
            if len(buf) > MAX_LINE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"JSONL frame exceeds {MAX_LINE_BYTES} bytes",
                )
            try:
                frame = json.loads(buf)
            except json.JSONDecodeError as e:
                _diag(f"parse fail (final): line={buf[:200]!r} err={e}")
                raise HTTPException(status_code=400, detail="invalid JSONL frame")
            count += 1
            await _dispatch_frame(request, sid, frame)
    except ClientDisconnect:
        # The runner closed its end mid-stream. Distinguish the empty-body
        # case (499, nginx "client closed request" convention) from the
        # partial-progress case (204, frames were dispatched). The runner's
        # reconnect loop in `runner/channel.py` makes this non-fatal either
        # way.
        _diag(
            f"rx disconnect: session_id={sid} bytes={total_bytes} "
            f"chunks={chunk_count} frames={count}"
        )
        if count == 0:
            return Response(status_code=499)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    _diag(
        f"rx close: session_id={sid} bytes={total_bytes} "
        f"chunks={chunk_count} frames={count}"
    )
    return JSONResponse(
        content={"received": count},
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.get("/sessions/{session_id}/inbox")
async def runner_inbox(
    session_id: uuid.UUID,
    request: Request,
    timeout_s: int = 25,
) -> Response:
    """Long-poll for outbound user messages headed to the runner.

    Backend-side queue is owned by RunnerRegistry. The registry's
    inbox_get already enforces the long-poll budget internally and
    returns None on timeout or when no entry exists for the session.
    """
    timeout_s = max(1, min(timeout_s, 30))
    registry = getattr(request.app.state, "runner_registry", None)

    if registry is None:
        # Stub mode (no real registry wired). Honour the budget and 204.
        await asyncio.sleep(timeout_s)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    msg: dict[str, Any] | None = await registry.inbox_get(
        str(session_id), timeout_s
    )
    if msg is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(content=msg, status_code=status.HTTP_200_OK)
