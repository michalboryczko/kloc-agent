"""Tests for `src/api/stream.py`.

Regression: `_persist_events` was started via `asyncio.create_task` with
no reference held and no done-callback, so any exception (UNIQUE
violation in `messages.seq`, pool checkout timeout, etc.) was swallowed
by asyncio's default exception handler. Empty `assistant_content`
silently landed in the DB.

Fix: `_log_persist_task_result` is the done-callback that surfaces
exceptions via `log.exception`.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import types
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

pytestmark = pytest.mark.unit


async def test_log_persist_task_result_logs_exceptions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from src.api.stream import _log_persist_task_result

    async def boom():
        raise RuntimeError("kaboom")

    task = asyncio.create_task(boom())
    with contextlib.suppress(RuntimeError):
        await task

    with caplog.at_level(logging.ERROR, logger="src.api.stream"):
        _log_persist_task_result(task)

    assert any(
        "_persist_events_failed" in r.message for r in caplog.records
    )


async def test_log_persist_task_result_ignores_clean_completion() -> None:
    from src.api.stream import _log_persist_task_result

    async def ok():
        return None

    task = asyncio.create_task(ok())
    await task
    _log_persist_task_result(task)


async def test_log_persist_task_result_ignores_cancellation() -> None:
    from src.api.stream import _log_persist_task_result

    async def long_running():
        await asyncio.sleep(60)

    task = asyncio.create_task(long_running())
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    _log_persist_task_result(task)


# ---------------------------------------------------------------------------
# ISS-02 regression: concurrent POST /stream for same (sid, rid) must NOT
# double-spawn the persister task. Pre-fix code created a fresh
# asyncio.create_task(_persist_events(...)) on every POST, so two concurrent
# reconnects against the same in-flight run produced two persisters that
# each appended every runner event to the execution ring (double-counted)
# and raced two `message_uuid` dicts on the first delta.
# ---------------------------------------------------------------------------


@dataclass
class _FakeWarmIdle:
    calls: int = 0

    def on_user_message(self) -> None:
        self.calls += 1


@dataclass
class _FakeEntry:
    inbox: asyncio.Queue
    warm_idle: _FakeWarmIdle


class _FakeRegistry:
    """Minimal RunnerRegistry stand-in: get_or_spawn returns a static entry
    with an inbox queue and a no-op warm-idle manager. No DB, no Docker."""

    def __init__(self) -> None:
        self._entry = _FakeEntry(
            inbox=asyncio.Queue(), warm_idle=_FakeWarmIdle()
        )

    async def get_or_spawn(self, session_id: str, payload: Any):
        return self._entry


class _FakeAppState:
    def __init__(self) -> None:
        self.runner_registry = _FakeRegistry()


class _FakeApp:
    def __init__(self) -> None:
        self.state = _FakeAppState()


class _FakeRequest:
    """Duck-typed Request: only the attributes stream_post actually
    touches before returning. `make_response` is monkeypatched out so
    headers / accept handling never run in this unit test."""

    def __init__(self, app: _FakeApp, body: dict) -> None:
        self.app = app
        self._body = body
        self.headers: dict[str, str] = {}

    async def json(self) -> dict:
        return self._body


async def test_concurrent_reconnect_does_not_double_spawn_persister(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ISS-02 regression: two concurrent stream_post calls for the same
    (session_id, run_id) result in exactly ONE _persist_events task,
    tracked at the (sid, rid) key in app.state.persist_tasks."""
    from src.api import stream as stream_mod

    counter = {"n": 0}

    async def _fake_persister(**_kwargs) -> None:
        # Increment the spawn counter and stay alive so the dedup-check
        # in stream_post sees the task as not-done on the second call.
        counter["n"] += 1
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            raise

    async def _noop_persist_user(*_args, **_kwargs) -> None:
        return None

    async def _fake_build_hydration(**_kwargs) -> Any:
        # stream_post forwards this opaquely to registry.get_or_spawn, which
        # is the FakeRegistry above and ignores the payload entirely.
        return types.SimpleNamespace()

    def _fake_make_response(_req, generator) -> dict:
        # Skip the real StreamingResponse (which needs an AG-UI encoder).
        # The assertion target is persist_tasks, not the SSE body. Close
        # the generator so it doesn't leak.
        return {"generator": generator}

    monkeypatch.setattr(stream_mod, "_persist_events", _fake_persister)
    monkeypatch.setattr(
        stream_mod, "_persist_user_message", _noop_persist_user
    )
    monkeypatch.setattr(
        stream_mod, "_build_hydration_payload", _fake_build_hydration
    )
    monkeypatch.setattr(stream_mod, "make_response", _fake_make_response)

    sid = str(uuid.uuid4())
    rid = "rA"
    body = {"runId": rid, "messages": [{"role": "user", "content": "hi"}]}
    app = _FakeApp()
    req_a = _FakeRequest(app, body)
    req_b = _FakeRequest(app, body)

    results = await asyncio.gather(
        stream_mod.stream_post(req_a, sid),
        stream_mod.stream_post(req_b, sid),
    )

    # Let the freshly-scheduled persister task reach its first await so
    # `existing.done()` would be False for the second concurrent caller.
    await asyncio.sleep(0)

    try:
        assert counter["n"] == 1, (
            f"expected exactly one persister spawn, got {counter['n']}"
        )

        persist_tasks = app.state.persist_tasks
        assert isinstance(persist_tasks, dict)
        assert len(persist_tasks) == 1
        assert (sid, rid) in persist_tasks
        assert not persist_tasks[(sid, rid)].done()

        # Both calls returned a (stub) response — neither raised.
        assert results[0] is not None
        assert results[1] is not None
    finally:
        # Cancel the long-lived fake persister so the test loop closes
        # cleanly.
        task = app.state.persist_tasks.get((sid, rid))
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, BaseException):
                await task
