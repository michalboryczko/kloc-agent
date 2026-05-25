"""Backend↔Runner internal API.

POST /internal/sessions/{id}/events   chunked JSONL ingress (runner → backend)

Localhost-only inside the compose bridge; no public auth.

Owns the transport: parse JSONL frames off the wire and publish AG-UI
events into the in-proc `EventBus` keyed by `(session_id, run_id)`.
Lifecycle frames (`heartbeat`, `RunnerHeartbeat`, `runner_ready`) reset
the runner registry's heartbeat watcher; everything else flows to the
bus. `runner_ready` is the runner's very first emitted frame and counts
as the cold-start beat at the ingress.

AG-UI events carry `runId` (camelCase); runner-internal liveness frames
carry `type: "heartbeat"`.

Runner-bound messages travel over PGMQ + LISTEN/NOTIFY (see
`src/messaging/pgmq.py`); there is no HTTP long-poll endpoint here.
"""
from __future__ import annotations

import functools
import json
import logging
import sys
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response
from starlette.requests import ClientDisconnect

from src.settings import get_settings
from src.shared.transport_limits import MAX_LINE_BYTES
from src.streaming.execution_registry import execution_registry


router = APIRouter(tags=["internal"])
log = logging.getLogger("kloc_agent.internal")


_BEAT_FRAME_TYPES = frozenset({"heartbeat", "RunnerHeartbeat", "runner_ready"})


try:
    from opentelemetry import metrics as _otel_metrics

    _meter = _otel_metrics.get_meter("kloc_agent.runner.channel")
    _frame_rejected_counter = _meter.create_counter(
        "kloc_agent.runner.frame_rejected_total",
        description="Inbound runner JSONL frames dropped at the ingress cap-check boundary.",
    )
    _frame_bytes_histogram = _meter.create_histogram(
        "kloc_agent.runner.frame_bytes",
        description="Byte size of inbound runner JSONL frames at the ingress cap-check boundary.",
        unit="By",
    )
    _spawn_to_first_beat_hist = _meter.create_histogram(
        "kloc_agent.runner.spawn_to_first_beat_s",
        description=(
            "Seconds from runner_spawned audit emit to the first "
            "beat-equivalent frame (runner_ready / heartbeat / "
            "RunnerHeartbeat) on the JSONL ingress."
        ),
        unit="s",
    )
except Exception:  # pragma: no cover - OTel absence must not crash boot
    _frame_rejected_counter = None
    _frame_bytes_histogram = None
    _spawn_to_first_beat_hist = None


_runner_spawn_ts: dict[str, float] = {}
_first_beat_recorded: set[str] = set()


def record_runner_spawned(session_id: str) -> None:
    """Stamp `time.monotonic()` so the ingress can compute spawn→first-beat.

    Called from the registry at the same point the `runner_spawned`
    audit row is emitted. Process-local — single uvicorn worker.
    """
    _runner_spawn_ts[session_id] = time.monotonic()
    _first_beat_recorded.discard(session_id)


def _maybe_record_first_beat(session_id: str) -> None:
    if session_id in _first_beat_recorded:
        return
    spawn_ts = _runner_spawn_ts.get(session_id)
    if spawn_ts is None:
        return
    elapsed = time.monotonic() - spawn_ts
    _first_beat_recorded.add(session_id)
    if _spawn_to_first_beat_hist is not None:
        try:
            _spawn_to_first_beat_hist.record(elapsed)
        except Exception:  # pragma: no cover - metric backend optional
            log.exception("spawn_to_first_beat_hist_failed")


@functools.lru_cache(maxsize=1)
def _diag_enabled() -> bool:
    # Settings construction may raise (e.g. missing provider key); diagnostics
    # are best-effort, so a build failure means "off", never a crash.
    try:
        return bool(get_settings().diag_events)
    except Exception:
        return False


def _diag(msg: str) -> None:
    """Off by default. Direct stderr bypasses uvicorn's --log-config
    which can filter `kloc_agent.*` INFO records in some images."""
    if not _diag_enabled():
        return
    print(msg, file=sys.stderr, flush=True)


async def _stamp_seq_and_publish(
    bus: Any, session_id: str, run_id: str, frame: dict
) -> None:
    """Allocate a monotonic seq from the ExecutionRegistry ring, stamp
    it onto the wire frame, then publish to the bus. Single source of
    seq truth — SSE subscribers and the persister both see the same
    number, and reconnect cursors stay consistent across replay."""
    execution = await execution_registry.get_or_create(session_id, run_id)
    seq = execution.append(frame)
    frame["seq"] = seq
    await bus.publish(session_id, run_id, frame)


async def _dispatch_frame(request: Request, session_id: str, frame: dict) -> None:
    """Route one JSONL frame to the right consumer.

    - `type: "heartbeat"` / `"RunnerHeartbeat"` / `"runner_ready"` →
      `RunnerRegistry.on_heartbeat_frame(session_id)`. Never published
      to the event bus — these are liveness signals, not AG-UI events.
      `runner_ready` is the runner's first emitted frame and counts as
      the cold-start beat.
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

    if frame_type in _BEAT_FRAME_TYPES:
        _maybe_record_first_beat(session_id)
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

    await _stamp_seq_and_publish(bus, session_id, str(run_id), frame)
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
                await _stamp_seq_and_publish(bus, session_id, str(run_id), buf)
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


# Cap on frames buffered per-session before RUN_STARTED is observed.
# Bounded so a misbehaving runner that never emits RUN_STARTED cannot
# OOM the backend.
_PRE_RUN_BUFFER_CAP = 1024


async def _emit_frame_rejected(
    request: Request,
    session_id: str,
    *,
    cause: str,
    upstream_status: int,
    byte_size: int,
) -> None:
    """Audit + OTel signals on a dropped inbound frame.

    The runner-id header is the source of truth for which runner sent
    the rejected bytes; the run_id is unknown at the cap-check boundary
    because the frame was never parsed.
    """
    runner_id = request.headers.get("X-Kloc-Runner-Id") or ""
    payload = {
        "session_id": session_id,
        "runner_id": runner_id,
        "run_id": None,
        "cause": cause,
        "upstream_status": upstream_status,
        "byte_size": byte_size,
    }
    if _frame_rejected_counter is not None:
        try:
            _frame_rejected_counter.add(1, {"cause": cause})
        except Exception:  # pragma: no cover - metric backend optional
            log.exception("frame_rejected_counter_failed")
    if _frame_bytes_histogram is not None:
        try:
            _frame_bytes_histogram.record(byte_size)
        except Exception:  # pragma: no cover
            log.exception("frame_bytes_histogram_failed")
    audit_emit = getattr(request.app.state, "audit_emit", None)
    if audit_emit is not None:
        try:
            await audit_emit("runner_channel_frame_rejected", payload)
        except Exception:  # pragma: no cover - audit best-effort
            log.exception("audit_emit_frame_rejected_failed")


def _oversize_response(byte_size: int) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "reason": "frame_oversized",
            "byte_size": byte_size,
            "cap": MAX_LINE_BYTES,
        },
    )


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
                    await _emit_frame_rejected(
                        request,
                        sid,
                        cause="frame_oversized",
                        upstream_status=413,
                        byte_size=len(line),
                    )
                    return _oversize_response(len(line))
                if _frame_bytes_histogram is not None:
                    try:
                        _frame_bytes_histogram.record(len(line))
                    except Exception:  # pragma: no cover
                        log.exception("frame_bytes_histogram_failed")
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError as e:
                    _diag(f"parse fail: line={line[:200]!r} err={e}")
                    return JSONResponse(
                        status_code=400,
                        content={"reason": "invalid_jsonl"},
                    )
                count += 1
                await _dispatch_frame(request, sid, frame)
            if len(buf) > MAX_LINE_BYTES:
                # Pending line (no newline yet) already over cap — fail fast
                # so we don't accumulate bytes indefinitely.
                await _emit_frame_rejected(
                    request,
                    sid,
                    cause="frame_oversized",
                    upstream_status=413,
                    byte_size=len(buf),
                )
                return _oversize_response(len(buf))

        # Final flush — handle a trailing line without a newline.
        buf = buf.strip()
        if buf:
            if len(buf) > MAX_LINE_BYTES:
                await _emit_frame_rejected(
                    request,
                    sid,
                    cause="frame_oversized",
                    upstream_status=413,
                    byte_size=len(buf),
                )
                return _oversize_response(len(buf))
            if _frame_bytes_histogram is not None:
                try:
                    _frame_bytes_histogram.record(len(buf))
                except Exception:  # pragma: no cover
                    log.exception("frame_bytes_histogram_failed")
            try:
                frame = json.loads(buf)
            except json.JSONDecodeError as e:
                _diag(f"parse fail (final): line={buf[:200]!r} err={e}")
                return JSONResponse(
                    status_code=400,
                    content={"reason": "invalid_jsonl"},
                )
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


