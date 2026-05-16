"""Audit-webhook sender.

`BeforeToolCallEvent` posts to `/v1/webhooks/runners/{runner_id}/events`
with an HMAC-signed body and a 2 s deadline.

Behaviour:
  1. Before*: blocks until the backend responds OR 2 s.
  2. On 2 s timeout: `event.cancel_tool = "policy_deadline_exceeded"`
     and emit a `CustomEvent(name="HookBackpressure", ...)` on the
     AG-UI stream.
  3. On `{decision: "deny", reason}`: `event.cancel_tool = reason`.
  4. After*: fire-and-forget; bounded queue of 256; drop heartbeats
     first when full; never drop Before*.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Awaitable, Callable

import httpx

from .utils import resolve_tool_call

log = logging.getLogger(__name__)

HOOK_DEADLINE_S = 2.0


def _sign(body: bytes, ts_ms: int, secret: str) -> str:
    canonical = f"{ts_ms}.".encode("utf-8") + body
    mac = hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


class AuditHookSender:
    """Owns the httpx client + the fire-and-forget After* queue."""

    def __init__(
        self,
        backend_url: str,
        runner_id: str,
        session_id: str,
        run_id_provider: Callable[[], str],
        runner_secret: str,
        emit_custom_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._runner_id = runner_id
        self._session_id = session_id
        self._run_id_provider = run_id_provider
        self._runner_secret = runner_secret
        self._emit_custom_event = emit_custom_event
        self._after_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)
        self._after_worker: asyncio.Task | None = None
        self._http: httpx.AsyncClient | None = None
        self._dropped_once = False

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=HOOK_DEADLINE_S)
        if self._after_worker is None or self._after_worker.done():
            self._after_worker = asyncio.create_task(self._after_loop())

    async def stop(self) -> None:
        # Cancel the worker FIRST so it stops competing with the drain
        # for queue items. If the worker had grabbed an item via
        # `await self._after_queue.get()` before stop() was called, it
        # is now either: (a) past its `await self._post(...)` (row
        # already persisted, nothing to do), or (b) suspended inside
        # `_post` and gets a CancelledError on the next await
        # boundary, which discards the in-flight item. (b) is an
        # acceptable loss of one row; the alternative (drain first)
        # races with the worker and can lose the SAME item via a
        # different code path while ALSO making drain non-exclusive,
        # making the race harder to reason about. After cancel, drain
        # under exclusive ownership of the queue. The per-request
        # timeout on self._http bounds each POST; total wall-clock is
        # at most HOOK_DEADLINE_S * queue_len.
        if self._after_worker:
            self._after_worker.cancel()
            try:
                await self._after_worker
            except asyncio.CancelledError:
                pass
        if self._http is not None:
            while True:
                try:
                    payload = self._after_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    await self._post(payload, "AfterToolCall")
                except Exception:
                    log.exception("audit.after_post_failed_during_drain")
        if self._http:
            await self._http.aclose()
            self._http = None

    async def before_tool_call(self, event: Any) -> None:
        """Block until the backend responds or the 2 s deadline lapses."""
        tool_name, tool_input = resolve_tool_call(event)
        log.info(
            "audit hook fired: BeforeToolCall tool=%s session=%s run=%s",
            tool_name,
            self._session_id,
            self._run_id_provider(),
        )
        payload = self._build_payload(
            event_name="BeforeToolCall",
            payload={
                "tool_call_id": event.tool_use.get("toolUseId")
                or event.tool_use.get("id"),
                "tool_name": tool_name,
                "args": tool_input,
            },
        )
        try:
            response = await self._post(payload, "BeforeToolCall")
        except (httpx.TimeoutException, asyncio.TimeoutError):
            event.cancel_tool = "policy_deadline_exceeded"
            await self._emit_custom_event(
                {
                    "type": "CUSTOM",
                    "name": "HookBackpressure",
                    "value": {
                        "event": "BeforeToolCall",
                        "tool": tool_name,
                        "reason": "deadline",
                    },
                }
            )
            return
        except Exception:
            log.exception("audit.before_post_failed")
            # Fail-closed on unexpected errors so a misconfigured backend
            # cannot let unaudited tool calls through silently.
            event.cancel_tool = "policy_unavailable"
            return
        if response.get("decision") == "deny":
            event.cancel_tool = response.get("reason", "policy_denied")

    async def after_tool_call(self, event: Any) -> None:
        """Fire-and-forget; drop heartbeats first when the queue
        overflows; never drop Before*."""
        tool_name, tool_input = resolve_tool_call(event)
        log.info(
            "audit hook fired: AfterToolCall tool=%s session=%s run=%s",
            tool_name,
            self._session_id,
            self._run_id_provider(),
        )
        result = getattr(event, "result", None)
        payload = self._build_payload(
            event_name="AfterToolCall",
            payload={
                "tool_call_id": event.tool_use.get("toolUseId")
                or event.tool_use.get("id"),
                "tool_name": tool_name,
                "result_preview": _preview(result),
            },
        )
        try:
            self._after_queue.put_nowait(payload)
        except asyncio.QueueFull:
            if not self._dropped_once:
                self._dropped_once = True
                await self._emit_custom_event(
                    {
                        "type": "CUSTOM",
                        "name": "HookBackpressure",
                        "value": {
                            "event": "AfterToolCall",
                            "tool": tool_name,
                            "reason": "queue_full",
                        },
                    }
                )

    def _build_payload(self, event_name: str, payload: dict) -> dict:
        return {
            "event": event_name,
            "runner_id": self._runner_id,
            "session_id": self._session_id,
            "run_id": self._run_id_provider(),
            "timestamp": int(time.time() * 1000),
            "payload": payload,
        }

    async def _post(self, payload: dict, event_name: str) -> dict:
        assert self._http is not None
        body = json.dumps(payload).encode("utf-8")
        ts_ms = payload["timestamp"]
        sig = _sign(body, ts_ms, self._runner_secret)
        headers = {
            "Authorization": f"HMAC {sig}",
            "X-Kloc-Hook-Event": event_name,
            "X-Kloc-Hook-Ts": str(ts_ms),
            "Content-Type": "application/json",
        }
        url = (
            f"{self._backend_url}/v1/webhooks/runners/{self._runner_id}/events"
        )
        log.info(
            "audit hook post -> %s [event=%s bytes=%d]",
            url,
            event_name,
            len(body),
        )
        try:
            r = await self._http.post(url, content=body, headers=headers)
        except Exception as exc:
            log.warning(
                "audit hook post failed -> %s [event=%s err=%r]",
                url,
                event_name,
                exc,
            )
            raise
        log.info(
            "audit hook post done  -> %s [event=%s status=%d]",
            url,
            event_name,
            r.status_code,
        )
        r.raise_for_status()
        return r.json()

    async def _after_loop(self) -> None:
        while True:
            payload = await self._after_queue.get()
            try:
                await self._post(payload, "AfterToolCall")
            except Exception:
                log.exception("audit.after_post_failed")


def _preview(result: Any) -> str | None:
    if result is None:
        return None
    try:
        text = json.dumps(result) if not isinstance(result, str) else result
    except Exception:
        text = repr(result)
    return text[:512]
