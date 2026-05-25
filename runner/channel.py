"""Runner -> backend channel.

Outbound-only. Two flows:
  * `emit(event)` enqueues; a background task streams events as JSONL
    over a single chunked `POST /internal/sessions/{id}/events`.
  * heartbeat: emits `{"type":"heartbeat","busy":bool,"ts":...}` every
    15 s, regardless of activity.

Backend -> runner traffic is handled separately by the runner's PGMQ
consumer module, not by this channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Final

import httpx

from src.shared.transport_limits import (
    MAX_LINE_BYTES as _BACKEND_MAX_LINE_BYTES,
    RUNNER_MAX_FRAME_BYTES as _SHARED_RUNNER_MAX_FRAME_BYTES,
)

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 15.0

RUNNER_MAX_FRAME_BYTES: Final[int] = _SHARED_RUNNER_MAX_FRAME_BYTES

# Backend deterministically rejects these statuses — retrying the same
# frame would just produce the same rejection. Drop the frame and emit
# a single `RUN_ERROR` instead of poisoning the channel with infinite
# replay. 429 is intentionally NOT in this set: rate-limiting is by
# definition a try-again-later condition, not a "this frame is bad"
# condition.
PERMANENT_REJECT_STATUSES: Final[frozenset[int]] = frozenset(
    {400, 413, 414, 415, 422}
)

# Sanity check: a frame the runner accepts MUST be one the backend
# accepts. The shared module is the only place where either constant
# is defined; if a future refactor introduces a divergence the runner
# would otherwise emit frames its own backend rejects.
assert RUNNER_MAX_FRAME_BYTES < _BACKEND_MAX_LINE_BYTES


OffloadCallback = Callable[[dict], Awaitable[dict | None]]


class BackendChannel:
    def __init__(
        self,
        backend_url: str,
        session_id: str,
        runner_id: str,
        run_id_provider,
        offload_oversize: OffloadCallback | None = None,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._session_id = session_id
        self._runner_id = runner_id
        self._run_id_provider = run_id_provider
        self._outbound: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1024)
        self._busy = False
        self._outbound_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None
        self._offload_oversize: OffloadCallback | None = offload_oversize

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=None)
        if self._outbound_task is None or self._outbound_task.done():
            self._outbound_task = asyncio.create_task(self._stream_outbound())
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        # Sentinel signals the outbound stream to close cleanly.
        await self._outbound.put(None)
        for task in (self._outbound_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._http:
            await self._http.aclose()
            self._http = None

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def set_offload_oversize(self, cb: OffloadCallback | None) -> None:
        """Wire the artifact-upload offload after construction.

        The audit-hook sender owns the HMAC + artifact webhook plumbing;
        the channel only needs a callable that takes an AG-UI frame and
        returns the rewritten frame (or None to drop). Set after both
        the channel and the hook sender exist.
        """
        self._offload_oversize = cb

    async def emit(self, event: dict) -> None:
        evt_type = event.get("type", "<unknown>")
        rewritten = await self._offload_if_oversize(event)
        if rewritten is None:
            log.warning(
                "channel emit dropped: type=%s reason=offload_failed",
                evt_type,
            )
            return
        log.info(
            "channel emit: type=%s run_id=%s busy=%s",
            evt_type,
            self._run_id_provider() if callable(self._run_id_provider) else None,
            self._busy,
        )
        await self._outbound.put(rewritten)

    async def _offload_if_oversize(self, event: dict) -> dict | None:
        """Route oversized frames through the artifact-upload path.

        The runner refuses to enqueue any frame larger than
        `RUNNER_MAX_FRAME_BYTES`; the offload callback uploads the
        oversized payload to S3 via the existing `ArtifactRegistered`
        webhook and returns a rewritten frame whose body references
        the artifact id. Returns None when the frame is oversized and
        the callback declined to handle it — the channel logs and
        drops in that case rather than poisoning itself.
        """
        try:
            serialised_len = len(json.dumps(event).encode("utf-8"))
        except (TypeError, ValueError):
            return event
        if serialised_len <= RUNNER_MAX_FRAME_BYTES:
            return event
        return await self._offload_oversize_frame(event, serialised_len)

    async def _offload_oversize_frame(
        self, event: dict, serialised_len: int
    ) -> dict | None:
        if self._offload_oversize is None:
            log.error(
                "channel emit: oversize frame with no offload callback "
                "configured; dropping. type=%s bytes=%d cap=%d",
                event.get("type", "<unknown>"),
                serialised_len,
                RUNNER_MAX_FRAME_BYTES,
            )
            return None
        try:
            rewritten = await self._offload_oversize(event)
        except Exception:
            log.exception("channel offload failed; dropping frame")
            return None
        if rewritten is None:
            return None
        try:
            rewritten_len = len(json.dumps(rewritten).encode("utf-8"))
        except (TypeError, ValueError):
            log.error("channel offload returned non-serialisable frame")
            return None
        if rewritten_len > RUNNER_MAX_FRAME_BYTES:
            log.error(
                "channel offload returned still-oversize frame; dropping. "
                "bytes=%d cap=%d",
                rewritten_len,
                RUNNER_MAX_FRAME_BYTES,
            )
            return None
        return rewritten

    async def _stream_outbound(self) -> None:
        """Single chunked POST whose body is a long-lived JSONL stream.

        Reconnect loop: on a transient transport-level failure
        (`httpx.TransportError`, response 408, 5xx) prepend the
        broken-attempt transcript and reconnect with backoff. On a
        permanent rejection (status in `PERMANENT_REJECT_STATUSES`)
        drop the offending frame, emit a synthetic `RUN_ERROR`, and
        keep draining the queue — replaying a deterministically
        rejected frame just produces the same rejection on every
        attempt and stalls the channel forever.
        """
        assert self._http is not None
        url = (
            f"{self._backend_url}/internal/sessions/"
            f"{self._session_id}/events"
        )

        pending_after_break: list[dict] = []

        # Frames yielded into httpx during the CURRENT attempt. Buffered
        # so a transient failure can replay them all on the next attempt
        # (over-delivery is preferable to silent loss; the backend
        # tolerates duplicate AG-UI intermediates). On a permanent
        # rejection only the LAST yielded frame is the offender — the
        # preceding ones were accepted across the TCP boundary, so we
        # prepend everything except the last back into pending.
        yielded_this_attempt: list[dict] = []

        async def body_iter():
            yielded_this_attempt.clear()
            while pending_after_break:
                event = pending_after_break.pop(0)
                yielded_this_attempt.append(event)
                line = (json.dumps(event) + "\n").encode("utf-8")
                yield line

            while True:
                event = await self._outbound.get()
                if event is None:
                    return
                yielded_this_attempt.append(event)
                line = (json.dumps(event) + "\n").encode("utf-8")
                log.info(
                    "channel send: type=%s bytes=%d",
                    event.get("type", "<unknown>"),
                    len(line),
                )
                yield line

        backoff = 0.5
        max_backoff = 10.0
        sentinel_seen = False

        while not sentinel_seen:
            log.info("channel stream opening -> %s", url)
            try:
                async with self._http.stream(
                    "POST",
                    url,
                    content=body_iter(),
                    headers={
                        "Content-Type": "application/x-ndjson",
                        "X-Kloc-Runner-Id": self._runner_id,
                    },
                    timeout=None,
                ) as response:
                    log.info(
                        "channel stream open <- %s [status=%d]",
                        url,
                        response.status_code,
                    )
                    if response.status_code >= 400:
                        if self._is_permanent(response.status_code):
                            sentinel_seen = self._handle_permanent_reject(
                                response.status_code,
                                yielded_this_attempt,
                                pending_after_break,
                            )
                            if sentinel_seen:
                                return
                            continue
                        log.error(
                            "channel.outbound_transient_status",
                            extra={"status": response.status_code},
                        )
                        pending_after_break[:0] = yielded_this_attempt
                        yielded_this_attempt.clear()
                        sentinel_seen = self._drain_into(pending_after_break)
                        if sentinel_seen:
                            return
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue
                sentinel_seen = True
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("channel.outbound_failed; reconnecting")
                pending_after_break[:0] = yielded_this_attempt
                yielded_this_attempt.clear()
                sentinel_seen = self._drain_into(pending_after_break)
                if sentinel_seen:
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    @staticmethod
    def _is_permanent(status: int) -> bool:
        if status in PERMANENT_REJECT_STATUSES:
            return True
        # Treat unknown 4xx (other than transient 408) as permanent.
        # 5xx and 408 stay transient.
        if 400 <= status < 500 and status != 408:
            return True
        return False

    def _handle_permanent_reject(
        self,
        status: int,
        yielded_this_attempt: list[dict],
        pending_after_break: list[dict],
    ) -> bool:
        """Drop the offending frame, replay the rest, emit RUN_ERROR.

        Returns True iff the outbound shutdown sentinel was encountered
        while draining the queue (caller must exit the stream loop).
        """
        if not yielded_this_attempt:
            log.error(
                "channel.outbound_permanent_status with no in-flight "
                "frame; nothing to drop. status=%d",
                status,
            )
            pending_after_break.extend(self._drain_to_list_returning_sentinel())
            return False
        offender = yielded_this_attempt.pop()
        accepted = list(yielded_this_attempt)
        yielded_this_attempt.clear()
        log.error(
            "channel.outbound_permanent_reject; dropping frame. "
            "status=%d type=%s",
            status,
            offender.get("type", "<unknown>"),
        )
        pending_after_break[:0] = accepted
        terminal = self._is_terminal_frame(offender)
        if not terminal:
            # The dropped frame is a non-terminal AG-UI event. Surface
            # a synthetic RUN_ERROR so SSE consumers see why the run
            # stopped; if the dropped frame WAS itself RUN_FINISHED or
            # RUN_ERROR the receiver already has a terminal frame and
            # adding another would double-emit.
            pending_after_break.append(
                self._build_run_error(status, offender)
            )
        sentinel_seen = self._drain_into(pending_after_break)
        return sentinel_seen

    def _drain_into(self, dest: list[dict]) -> bool:
        sentinel_seen = False
        while True:
            try:
                event = self._outbound.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event is None:
                sentinel_seen = True
                break
            dest.append(event)
        return sentinel_seen

    def _drain_to_list_returning_sentinel(self) -> list[dict]:
        out: list[dict] = []
        self._drain_into(out)
        return out

    @staticmethod
    def _is_terminal_frame(event: dict) -> bool:
        return event.get("type") in ("RUN_FINISHED", "RUN_ERROR")

    def _build_run_error(
        self, upstream_status: int, dropped: dict
    ) -> dict:
        run_id: Any = None
        if callable(self._run_id_provider):
            try:
                run_id = self._run_id_provider()
            except Exception:
                run_id = None
        return {
            "type": "RUN_ERROR",
            "runId": run_id,
            "value": {
                "cause": "frame_rejected",
                "upstreamStatus": upstream_status,
                "droppedType": dropped.get("type"),
            },
        }

    async def _heartbeat_loop(self) -> None:
        # First emit lands at t≈0 so the backend's heartbeat watcher
        # resets the moment the channel is up — otherwise a slow cold
        # start can outrun the watcher's first-beat budget.
        while True:
            await self.emit(
                {
                    "type": "heartbeat",
                    "session_id": self._session_id,
                    "runner_id": self._runner_id,
                    "ts": int(time.time() * 1000),
                    "busy": self._busy,
                }
            )
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            except asyncio.CancelledError:
                return
