"""Regression tests for AuditHookSender.stop() draining the AfterToolCall queue.

ISS-03: stop() previously cancelled the worker without draining `_after_queue`,
silently dropping up to 256 `tool_call.completed` audit rows per graceful
shutdown (including warm-idle eviction).

WR-05: a later iteration cancels the worker FIRST, then drains. Drain-first
raced the live worker for queue items: if the worker won `get()` on a
payload before drain started, that payload's in-flight POST got cancelled
mid-await and the row was lost anyway. Cancel-first makes the drain run
under exclusive ownership of the queue; the only loss is the at-most-one
item the worker had already grabbed (which would have been lost under
either ordering).

These tests assert every queued payload is POSTed during drain and that
the drain tolerates per-payload transport failures."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from runner.hooks.audit import AuditHookSender

pytestmark = pytest.mark.unit


class _FakeHttp:
    """Stand-in for httpx.AsyncClient used only to keep `_http is not None`
    and to provide an awaitable aclose()."""

    def __init__(self) -> None:
        self.aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


async def _emit_noop(_event: dict) -> None:
    return None


def _make_sender() -> AuditHookSender:
    return AuditHookSender(
        backend_url="http://unused",
        runner_id="rid",
        session_id="sid",
        run_id_provider=lambda: "rA",
        runner_secret="secret",
        emit_custom_event=_emit_noop,
    )


async def _install_idle_worker_and_http(sender: AuditHookSender) -> _FakeHttp:
    """Attach a no-op http client and a completed worker task so stop() runs
    its full drain+cancel+aclose sequence without touching the network."""
    http = _FakeHttp()
    sender._http = http  # type: ignore[assignment]
    # A pre-completed task: cancel() on a done task is a no-op and awaiting
    # it returns immediately, so the worker leg of stop() is exercised but
    # cannot race the drain.
    sender._after_worker = asyncio.create_task(asyncio.sleep(0))
    await asyncio.sleep(0)
    return http


async def test_stop_drains_after_queue_before_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = _make_sender()
    http = await _install_idle_worker_and_http(sender)

    calls: list[tuple[dict, str]] = []

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        calls.append((payload, event_name))
        return {}

    monkeypatch.setattr(AuditHookSender, "_post", _fake_post)

    for i in range(3):
        sender._after_queue.put_nowait({"event": "AfterToolCall", "marker": i})

    await sender.stop()

    assert len(calls) == 3
    assert [p["marker"] for p, _ in calls] == [0, 1, 2]
    assert all(name == "AfterToolCall" for _, name in calls)
    assert sender._after_queue.empty()
    assert sender._after_worker is not None and sender._after_worker.done()
    assert sender._http is None
    assert http.aclose_calls == 1


async def test_stop_continues_drain_on_post_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = _make_sender()
    await _install_idle_worker_and_http(sender)

    calls: list[tuple[dict, str]] = []

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        calls.append((payload, event_name))
        if len(calls) == 2:
            raise httpx.TimeoutException("simulated")
        return {}

    monkeypatch.setattr(AuditHookSender, "_post", _fake_post)

    for i in range(3):
        sender._after_queue.put_nowait({"event": "AfterToolCall", "marker": i})

    # Must not re-raise even when one POST inside the drain fails.
    await sender.stop()

    assert len(calls) == 3
    assert [p["marker"] for p, _ in calls] == [0, 1, 2]


async def test_stop_with_empty_queue_does_not_call_post(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = _make_sender()
    await _install_idle_worker_and_http(sender)

    calls: list[Any] = []

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        calls.append((payload, event_name))
        return {}

    monkeypatch.setattr(AuditHookSender, "_post", _fake_post)

    await sender.stop()

    assert calls == []
    assert sender._http is None


async def test_stop_cancels_worker_before_draining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WR-05 regression: when the worker is actively awaiting the next
    queue item, stop() must cancel it BEFORE the drain runs so the
    drain has exclusive ownership of the queue. With cancel-first, no
    queue item can be claimed by the worker after drain begins; with
    the old drain-first order, the worker could claim an item between
    drain iterations, then get cancelled mid-POST, losing the row.

    We verify ordering by recording the moment of worker-cancellation
    relative to each _post call. Every _post must run AFTER the worker
    task is done (cancelled)."""
    sender = _make_sender()
    http = _FakeHttp()
    sender._http = http  # type: ignore[assignment]

    # Real worker: parks on queue.get() until cancelled or item arrives.
    # NOT pre-completed -- so we can observe ordering.
    sender._after_worker = asyncio.create_task(sender._after_loop())
    await asyncio.sleep(0)  # let the worker reach `await get()`

    worker_done_when_called: list[bool] = []

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        worker_done_when_called.append(
            self._after_worker is not None and self._after_worker.done()
        )
        return {}

    monkeypatch.setattr(AuditHookSender, "_post", _fake_post)

    for i in range(3):
        sender._after_queue.put_nowait(
            {"event": "AfterToolCall", "marker": i}
        )

    await sender.stop()

    # Every _post invoked from the drain must have observed the worker
    # as already-done (cancelled). With the pre-fix order (drain first,
    # cancel later) this would record False on every call.
    assert worker_done_when_called == [True, True, True], (
        f"_post ran with worker still live: {worker_done_when_called}"
    )
    assert sender._after_queue.empty()
    assert sender._http is None


async def test_stop_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    sender = _make_sender()
    await _install_idle_worker_and_http(sender)

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        return {}

    monkeypatch.setattr(AuditHookSender, "_post", _fake_post)

    await sender.stop()
    # Second call: worker already cancelled, http already None, queue empty.
    await sender.stop()
