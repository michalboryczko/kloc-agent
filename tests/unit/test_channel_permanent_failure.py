"""Permanent vs transient outbound-channel failure split.

A response with a status in `PERMANENT_REJECT_STATUSES` (e.g. 422)
indicates a deterministically-rejected frame. Replaying it would just
produce the same rejection forever, so the channel must drop the
offending frame, emit one synthetic `RUN_ERROR`, and keep draining the
queue. A transient status (503) keeps the existing prepend-and-retry
behaviour and emits no synthetic `RUN_ERROR`.
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _StreamCM:
    """Async CM that advances the body iterator a configured number
    of times and returns either a successful response or a >=400 one."""

    def __init__(
        self,
        body_iter,
        collected: list[bytes],
        *,
        advance: int | None,
        status_code: int,
        terminate_queue=None,
    ) -> None:
        self._body = body_iter
        self._collected = collected
        self._advance = advance
        self._status_code = status_code
        self._terminate_queue = terminate_queue

    async def __aenter__(self) -> _FakeResponse:
        if self._advance is not None:
            for _ in range(self._advance):
                line = await self._body.__anext__()
                self._collected.append(line)
            return _FakeResponse(status_code=self._status_code)
        if self._terminate_queue is not None:
            self._terminate_queue.put_nowait(None)
        async for line in self._body:
            self._collected.append(line)
        return _FakeResponse(status_code=200)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeHttp:
    def __init__(self, attempts: list[dict], chan=None) -> None:
        self._attempts = attempts
        self._idx = 0
        self._chan = chan

    def stream(self, method, url, *, content, headers, timeout):
        if self._idx >= len(self._attempts):
            raise AssertionError(
                f"unexpected reconnect attempt #{self._idx + 1}"
            )
        spec = self._attempts[self._idx]
        self._idx += 1
        terminate_queue = (
            self._chan._outbound
            if (spec.get("terminate") and self._chan is not None)
            else None
        )
        return _StreamCM(
            body_iter=content,
            collected=spec["collected"],
            advance=spec.get("advance"),
            status_code=spec.get("status_code", 200),
            terminate_queue=terminate_queue,
        )


def _decode(lines: list[bytes]) -> list[dict]:
    return [json.loads(b.rstrip(b"\n")) for b in lines]


async def _run_outbound(chan, monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep

    async def _instant_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr("runner.channel.asyncio.sleep", _instant_sleep)
    await asyncio.wait_for(chan._stream_outbound(), timeout=5.0)


async def test_permanent_422_drops_offending_frame_emits_run_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )

    bad = {"type": "TEXT_MESSAGE_CONTENT", "delta": "schema-invalid"}
    a = {"type": "TEXT_MESSAGE_CONTENT", "delta": "A"}
    b = {"type": "TEXT_MESSAGE_CONTENT", "delta": "B"}
    await chan._outbound.put(bad)
    await chan._outbound.put(a)
    await chan._outbound.put(b)

    first: list[bytes] = []
    second: list[bytes] = []
    chan._http = _FakeHttp(
        attempts=[
            {
                "advance": 1,
                "collected": first,
                "status_code": 422,
            },
            {
                "advance": None,
                "collected": second,
                "terminate": True,
            },
        ],
        chan=chan,
    )

    await _run_outbound(chan, monkeypatch)

    assert _decode(first) == [bad]
    decoded = _decode(second)
    assert bad not in decoded, "permanent-reject frame must NOT be replayed"
    types = [d.get("type") for d in decoded]
    assert "RUN_ERROR" in types, (
        f"synthetic RUN_ERROR not emitted on permanent reject; got {types}"
    )
    run_errors = [d for d in decoded if d.get("type") == "RUN_ERROR"]
    assert len(run_errors) == 1, (
        f"expected exactly one synthetic RUN_ERROR, got {len(run_errors)}"
    )
    re = run_errors[0]
    assert re["value"]["cause"] == "frame_rejected"
    assert re["value"]["upstreamStatus"] == 422
    follow_ups = [d for d in decoded if d.get("type") == "TEXT_MESSAGE_CONTENT"]
    assert follow_ups == [a, b], (
        f"follow-up frames must be delivered in order, got {follow_ups}"
    )


async def test_transient_503_replays_and_emits_no_run_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    f = {"type": "TEXT_MESSAGE_CONTENT", "delta": "x"}
    await chan._outbound.put(f)

    first: list[bytes] = []
    second: list[bytes] = []
    chan._http = _FakeHttp(
        attempts=[
            {
                "advance": 1,
                "collected": first,
                "status_code": 503,
            },
            {
                "advance": None,
                "collected": second,
                "terminate": True,
            },
        ],
        chan=chan,
    )

    await _run_outbound(chan, monkeypatch)

    assert _decode(first) == [f]
    decoded = _decode(second)
    assert decoded == [f], f"transient retry must replay F exactly once, got {decoded}"
    assert not any(d.get("type") == "RUN_ERROR" for d in decoded)


async def test_permanent_reject_of_terminal_frame_emits_no_double_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the rejected frame is itself `RUN_FINISHED` the channel
    must not double-emit a synthetic `RUN_ERROR` — the receiver
    already has a terminal frame in its buffer (RUN_FINISHED was
    queued before the rejection from the producer side, and the
    AG-UI consumer tolerates the missing one)."""
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    rf = {"type": "RUN_FINISHED", "runId": "run1"}
    await chan._outbound.put(rf)
    await chan._outbound.put(None)

    first: list[bytes] = []
    chan._http = _FakeHttp(
        attempts=[
            {
                "advance": 1,
                "collected": first,
                "status_code": 422,
            },
        ],
        chan=chan,
    )

    await _run_outbound(chan, monkeypatch)

    assert _decode(first) == [rf]
    # The sentinel terminates the loop; no second attempt was made,
    # and crucially no synthetic RUN_ERROR was prepended.
