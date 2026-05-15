"""Runner <-> backend channel. Plan task D16.

Three flows:
  * outbound: `emit(event)` enqueues; a background task streams events
    as JSONL over a single chunked `POST /internal/sessions/{id}/events`.
  * inbound: `iter_inbound()` long-polls `GET /internal/sessions/{id}/inbox`
    with a 25 s budget (< 30 s heartbeat budget).
  * heartbeat: emits `{"type":"heartbeat","busy":bool,"ts":...}` every
    15 s, regardless of activity.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

import httpx

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 15.0
INBOX_POLL_TIMEOUT_S = 25.0


class BackendChannel:
    def __init__(
        self,
        backend_url: str,
        session_id: str,
        runner_id: str,
        run_id_provider,
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
                except (asyncio.CancelledError, BaseException):
                    pass
        if self._http:
            await self._http.aclose()
            self._http = None

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    async def emit(self, event: dict) -> None:
        # B-DIAG-B: log every emit at the channel boundary so we can
        # trace whether AG-UI events from the Strands stream loop are
        # reaching the outbound queue at all. Cheap: rare enough not to
        # spam (per-AG-UI-event, not per-byte) and helpful for diagnosing
        # the assistant-message-not-persisted gap.
        evt_type = event.get("type", "<unknown>")
        log.info(
            "CHANNEL EMIT: type=%s run_id=%s busy=%s",
            evt_type,
            self._run_id_provider() if callable(self._run_id_provider) else None,
            self._busy,
        )
        await self._outbound.put(event)

    async def iter_inbound(self) -> AsyncIterator[dict]:
        """Long-poll the backend inbox. Yields one message per poll
        response. Backend returns 204 on timeout; we just re-poll."""
        assert self._http is not None
        url = (
            f"{self._backend_url}/internal/sessions/"
            f"{self._session_id}/inbox"
        )
        while True:
            try:
                r = await self._http.get(
                    url, timeout=INBOX_POLL_TIMEOUT_S + 5
                )
            except httpx.TimeoutException:
                continue
            except Exception:
                log.exception("channel.inbox_poll_failed")
                await asyncio.sleep(1.0)
                continue
            if r.status_code == 204:
                continue
            if r.status_code != 200:
                log.warning(
                    "channel.inbox_unexpected_status",
                    extra={"status": r.status_code},
                )
                await asyncio.sleep(1.0)
                continue
            try:
                msg = r.json()
            except Exception:
                log.exception("channel.inbox_decode_failed")
                continue
            if msg.get("type") == "shutdown":
                return
            yield msg

    async def _stream_outbound(self) -> None:
        """Single chunked POST whose body is a long-lived JSONL stream.
        Plan §401."""
        assert self._http is not None
        url = (
            f"{self._backend_url}/internal/sessions/"
            f"{self._session_id}/events"
        )

        async def body_iter():
            while True:
                event = await self._outbound.get()
                if event is None:
                    return
                line = (json.dumps(event) + "\n").encode("utf-8")
                # B-DIAG-B: confirm the JSONL frame is leaving the runner
                # over the chunked POST body. If CHANNEL EMIT lines show
                # up but no CHANNEL SEND lines, the stream POST is stuck
                # (handshake failed / backend unreachable / etc.).
                log.info(
                    "CHANNEL SEND: type=%s bytes=%d",
                    event.get("type", "<unknown>"),
                    len(line),
                )
                yield line

        log.info("CHANNEL STREAM OPENING -> %s", url)  # B-DIAG-B
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
                    "CHANNEL STREAM OPEN <- %s [status=%d]",
                    url,
                    response.status_code,
                )  # B-DIAG-B
                if response.status_code >= 400:
                    log.error(
                        "channel.outbound_bad_status",
                        extra={"status": response.status_code},
                    )
        except Exception:
            log.exception("channel.outbound_failed")

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            except asyncio.CancelledError:
                return
            await self.emit(
                {
                    "type": "heartbeat",
                    "session_id": self._session_id,
                    "runner_id": self._runner_id,
                    "ts": int(time.time() * 1000),
                    "busy": self._busy,
                }
            )
