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
    warm_idle: _FakeWarmIdle


class _FakeRegistry:
    """Minimal RunnerRegistry stand-in: get_or_spawn returns a static entry
    with a no-op warm-idle manager. No DB, no Docker."""

    def __init__(self) -> None:
        self._entry = _FakeEntry(warm_idle=_FakeWarmIdle())

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


# ---------------------------------------------------------------------------
# CR-01 regression: the GET reconnect handler (`stream_get`) must not drop
# events that land between the replay-from-ring snapshot and the live-tail
# subscription. Pre-fix code did replay then subscribe — events appended
# in that window were never seen by the resumed client. Post-fix: register
# the queue FIRST, snapshot the ring, replay, then do a second-pass replay
# using the highest seen seq to close the remaining window, then consume.
# ---------------------------------------------------------------------------


async def test_stream_get_replay_then_live_does_not_drop_between_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window between `replay_from` and the subscriber being live on
    the bus must be covered by a second-pass replay so no event with
    `seq > last_event_id` is silently dropped on reconnect."""
    from src.api import stream as stream_mod
    from src.streaming.event_bus import event_bus
    from src.streaming.execution_registry import (
        Execution,
        execution_registry,
    )

    sid = str(uuid.uuid4())
    rid = "rGetSeam"
    execution = Execution(session_id=sid, run_id=rid)
    execution.append({"type": "RUN_STARTED", "runId": rid})
    execution.append(
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "a"}
    )
    # Inject the execution into the global registry so stream_get can
    # find it; ensure cleanup so other tests don't see this run id.
    async with execution_registry._lock:
        execution_registry._executions[(sid, rid)] = execution

    # Capture the generator that stream_get returns so we can drive it
    # in-test. The real `make_response` would wrap it in a
    # StreamingResponse + AG-UI encoder; we want the raw event stream.
    captured: dict[str, Any] = {}

    def _fake_make_response(_req, generator):
        captured["gen"] = generator
        return {"generator": generator}

    monkeypatch.setattr(stream_mod, "make_response", _fake_make_response)

    request = _FakeRequest(_FakeApp(), body={})

    # We need to interpose between "register" and "first replay" so we can
    # append a SEAM event AFTER the subscriber's queue exists but BEFORE
    # the generator pulls from the ring. Easiest: wrap `register` to
    # append a seam event to the ring as a side-effect right after the
    # queue is added to `_subs`. Pre-fix code (subscribe AFTER replay)
    # would publish the seam onto NO queue (queue not yet registered),
    # then replay snapshot would not include it because the ring was
    # captured before append. Post-fix: register-first guarantees the
    # publish path queues the event, AND a second-pass replay over the
    # ring (which the seam IS in) also yields it (de-dup is the caller's
    # problem — same as the multi-subscriber case).
    real_register = event_bus.register
    seam_event = {
        "type": "TEXT_MESSAGE_CONTENT",
        "messageId": "m1",
        "delta": "SEAM",
    }

    async def _register_then_append_seam(session_id, run_id):
        queue = await real_register(session_id, run_id)
        # Append AND publish, mirroring how `_dispatch_frame` does it:
        # ring append + bus publish in a single critical section.
        execution.append(seam_event)
        await event_bus.publish(session_id, run_id, seam_event)
        return queue

    monkeypatch.setattr(event_bus, "register", _register_then_append_seam)

    try:
        await stream_mod.stream_get(
            request=request,  # type: ignore[arg-type]
            session_id=sid,
            run_id=rid,
            last_event_id=None,
        )

        # Collect events the resumed client would see. Publish a terminal
        # frame so the consume loop exits naturally.
        gen = captured["gen"]

        async def _drain_and_terminate() -> list[dict]:
            collected: list[dict] = []

            async def _publish_terminal() -> None:
                # Let the generator start before we publish terminal.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await event_bus.publish(
                    sid, rid, {"type": "RUN_FINISHED", "runId": rid}
                )

            publisher = asyncio.create_task(_publish_terminal())
            try:
                async for evt in gen:
                    collected.append(evt)
            finally:
                if not publisher.done():
                    publisher.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await publisher
            return collected

        events = await asyncio.wait_for(_drain_and_terminate(), timeout=5.0)

        # The SEAM event must appear at least once. Pre-fix, the snapshot
        # would not contain it (taken before append) AND it would never
        # land on the queue (subscribe ran AFTER publish), so it would
        # be silently dropped from the resumed client's view.
        seam_seen = [e for e in events if e.get("delta") == "SEAM"]
        assert len(seam_seen) >= 1, (
            f"SEAM event lost across replay/live seam; saw events: "
            f"{[e.get('type') for e in events]}"
        )
        # Lifecycle terminal closed the stream.
        assert events[-1]["type"] == "RUN_FINISHED"
    finally:
        # Clean up the execution we injected so other tests don't see it.
        async with execution_registry._lock:
            execution_registry._executions.pop((sid, rid), None)
        # Drop any subscriber queues left behind on the bus topic.
        await event_bus.close(sid, rid)


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

    pgmq_calls: list[tuple[str, str, list]] = []

    async def _fake_ensure_inbox_queue(_conn, session_id):
        return f"inbox_{session_id.replace('-', '')}"

    async def _fake_send_user_message(_conn, session_id, run_id, messages):
        pgmq_calls.append((session_id, run_id, messages))
        return len(pgmq_calls)

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def connection(self):
            return None

        async def commit(self):
            return None

    def _fake_sessionmaker():
        return lambda: _FakeSession()

    monkeypatch.setattr(stream_mod, "_persist_events", _fake_persister)
    monkeypatch.setattr(
        stream_mod, "_persist_user_message", _noop_persist_user
    )
    monkeypatch.setattr(
        stream_mod, "_build_hydration_payload", _fake_build_hydration
    )
    monkeypatch.setattr(stream_mod, "make_response", _fake_make_response)
    monkeypatch.setattr(
        stream_mod, "ensure_inbox_queue", _fake_ensure_inbox_queue
    )
    monkeypatch.setattr(
        stream_mod, "send_user_message", _fake_send_user_message
    )
    monkeypatch.setattr(stream_mod, "get_sessionmaker", _fake_sessionmaker)

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

        assert len(pgmq_calls) == 2
        assert all(sid_ == sid and rid_ == rid for sid_, rid_, _ in pgmq_calls)
    finally:
        # Cancel the long-lived fake persister so the test loop closes
        # cleanly.
        task = app.state.persist_tasks.get((sid, rid))
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, BaseException):
                await task
