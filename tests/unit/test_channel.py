"""Tests for `runner/channel.py`.

Regression: the runner outbound stream previously had no reconnect — a
single transient backend close permanently silenced this runner. Fix:
on transport-level exceptions, drain the queue into a `pending_after_break`
list and reconnect with exponential backoff, replaying buffered events
on the next stream attempt.

These tests cover the queue-drain behaviour at the heart of the fix
without spinning up a real backend.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


async def test_drain_outbound_queue_into_pending_buffer() -> None:
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    await chan._outbound.put({"type": "TEXT_MESSAGE_CONTENT"})
    await chan._outbound.put({"type": "TEXT_MESSAGE_END"})
    await chan._outbound.put(None)  # sentinel signals graceful close

    pending: list[dict] = []
    sentinel_seen = False
    while True:
        try:
            e = chan._outbound.get_nowait()
        except asyncio.QueueEmpty:
            break
        if e is None:
            sentinel_seen = True
            break
        pending.append(e)

    assert sentinel_seen is True
    assert [e["type"] for e in pending] == [
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
    ]


async def test_emit_does_not_block_on_idle_queue() -> None:
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    await chan.emit({"type": "RUN_STARTED"})
    assert chan._outbound.qsize() == 1


# ---------------------------------------------------------------------------
# ISS-06 regression: mid-emit transport exception loses the in-flight frame
# unless _stream_outbound prepends `last_inflight` to pending_after_break.
# ---------------------------------------------------------------------------


import json  # noqa: E402

import httpx  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _FakeStreamCM:
    """Async CM standing in for `httpx.AsyncClient.stream(...)`.

    Two modes, selected per construction:
      * ``raise_after=N`` — advance the body iterator ``N`` times into
        ``collected``, then raise ``RemoteProtocolError`` from
        ``__aenter__`` (simulating mid-emit transport reset).
      * ``raise_after=None`` — drain the body iterator to completion
        into ``collected`` and return normally. If ``terminate_with``
        is provided, push it onto the outbound queue first so a body
        iter blocked on ``.get()`` unblocks via the ``None`` sentinel.
    """

    def __init__(
        self,
        body_iter,
        collected: list[bytes],
        raise_after: int | None,
        terminate_queue=None,
    ) -> None:
        self._body = body_iter
        self._collected = collected
        self._raise_after = raise_after
        self._terminate_queue = terminate_queue

    async def __aenter__(self) -> _FakeResponse:
        if self._raise_after is not None:
            for _ in range(self._raise_after):
                line = await self._body.__anext__()
                self._collected.append(line)
            raise httpx.RemoteProtocolError("simulated mid-emit reset")
        if self._terminate_queue is not None:
            # Push the sentinel BEFORE iterating so the body iter
            # returns naturally once pending_after_break is drained.
            self._terminate_queue.put_nowait(None)
        async for line in self._body:
            self._collected.append(line)
        return _FakeResponse(status_code=200)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeHttp:
    """Stand-in for ``httpx.AsyncClient`` exposing only ``.stream``.

    Each call constructs a fresh ``_FakeStreamCM`` driven by the
    ``attempts`` script: attempt 0 uses ``attempts[0]`` etc. After the
    script is exhausted any further calls raise — exposes accidental
    extra reconnects in the test.
    """

    def __init__(self, attempts: list[dict], chan=None) -> None:
        # attempts[i] = {
        #   "raise_after": int|None,
        #   "collected":   list[bytes],
        #   "terminate":   bool,  # push None sentinel into chan._outbound
        # }
        self._attempts = attempts
        self._call_index = 0
        self._chan = chan

    def stream(self, method, url, *, content, headers, timeout):
        if self._call_index >= len(self._attempts):
            raise AssertionError(
                f"unexpected reconnect attempt #{self._call_index + 1}"
            )
        spec = self._attempts[self._call_index]
        self._call_index += 1
        terminate_queue = (
            self._chan._outbound
            if (spec.get("terminate") and self._chan is not None)
            else None
        )
        return _FakeStreamCM(
            body_iter=content,
            collected=spec["collected"],
            raise_after=spec["raise_after"],
            terminate_queue=terminate_queue,
        )


def _decode_lines(lines: list[bytes]) -> list[dict]:
    return [json.loads(b.rstrip(b"\n")) for b in lines]


async def test_reconnect_replays_last_inflight_frame() -> None:
    """A frame yielded by body_iter on the first attempt but lost when
    the transport raises mid-block must be re-sent at the head of the
    second attempt — otherwise RUN_FINISHED can vanish (ISS-06)."""
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )

    e1 = {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}
    e2 = {"type": "TEXT_MESSAGE_END"}
    e3 = {"type": "RUN_FINISHED"}
    await chan._outbound.put(e1)
    await chan._outbound.put(e2)
    await chan._outbound.put(e3)

    first: list[bytes] = []
    second: list[bytes] = []
    chan._http = _FakeHttp(
        attempts=[
            {"raise_after": 1, "collected": first},
            {"raise_after": None, "collected": second, "terminate": True},
        ],
        chan=chan,
    )

    # Skip the backoff sleep so the test is fast. Capture the real
    # sleep BEFORE patching so the replacement does not recurse.
    real_sleep = asyncio.sleep

    async def _instant_sleep(_):
        await real_sleep(0)

    import runner.channel as _channel_mod

    orig_sleep = _channel_mod.asyncio.sleep
    _channel_mod.asyncio.sleep = _instant_sleep  # type: ignore[assignment]
    try:
        await asyncio.wait_for(chan._stream_outbound(), timeout=5.0)
    finally:
        _channel_mod.asyncio.sleep = orig_sleep  # type: ignore[assignment]

    # Attempt 1 yielded exactly e1 before the simulated reset.
    assert _decode_lines(first) == [e1]
    # Attempt 2: e1 must be replayed at the head, then e2, e3.
    decoded = _decode_lines(second)
    assert decoded[0]["type"] == "TEXT_MESSAGE_CONTENT"
    assert decoded == [e1, e2, e3]


async def test_clean_shutdown_does_not_resend_last_frame() -> None:
    """A normal None-sentinel close must not re-send the last frame on a
    later reconnect (there is no later reconnect — the function exits)."""
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    only = {"type": "RUN_FINISHED"}
    await chan._outbound.put(only)
    await chan._outbound.put(None)

    collected: list[bytes] = []
    chan._http = _FakeHttp(
        attempts=[{"raise_after": None, "collected": collected}],
        chan=chan,
    )

    await asyncio.wait_for(chan._stream_outbound(), timeout=5.0)

    assert _decode_lines(collected) == [only]


# ---------------------------------------------------------------------------
# CR-02 regression: the >= 400 status branch must apply the same drain +
# prepend pattern as the exception branch. Pre-fix code only prepended
# `last_inflight`, leaving any earlier-yielded frames lost on the next
# attempt because body_iter had already pulled them off `_outbound`.
# ---------------------------------------------------------------------------


class _BadStatusStreamCM:
    """Async CM whose `__aenter__` advances the body iterator some number
    of times and then returns a `>= 400` response without raising. This
    simulates the 4xx/5xx path that CR-02 was about (e.g. a transient 503
    during backend startup, a 404 mid-redeploy)."""

    def __init__(
        self,
        body_iter,
        collected: list[bytes],
        advance: int,
        status_code: int = 503,
    ) -> None:
        self._body = body_iter
        self._collected = collected
        self._advance = advance
        self._status_code = status_code

    async def __aenter__(self) -> _FakeResponse:
        for _ in range(self._advance):
            line = await self._body.__anext__()
            self._collected.append(line)
        return _FakeResponse(status_code=self._status_code)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _MixedHttp:
    """Like _FakeHttp but a per-attempt mode: 'bad_status' (CR-02) or
    'ok' (clean shutdown)."""

    def __init__(self, attempts: list[dict], chan=None) -> None:
        self._attempts = attempts
        self._call_index = 0
        self._chan = chan

    def stream(self, method, url, *, content, headers, timeout):
        if self._call_index >= len(self._attempts):
            raise AssertionError(
                f"unexpected reconnect attempt #{self._call_index + 1}"
            )
        spec = self._attempts[self._call_index]
        self._call_index += 1
        mode = spec["mode"]
        if mode == "bad_status":
            return _BadStatusStreamCM(
                body_iter=content,
                collected=spec["collected"],
                advance=spec["advance"],
                status_code=spec.get("status_code", 503),
            )
        if mode == "ok":
            terminate_queue = (
                self._chan._outbound
                if (spec.get("terminate") and self._chan is not None)
                else None
            )
            return _FakeStreamCM(
                body_iter=content,
                collected=spec["collected"],
                raise_after=None,
                terminate_queue=terminate_queue,
            )
        raise AssertionError(f"unknown mode {mode!r}")


async def test_4xx_response_drains_outbound_and_replays_all_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CR-02 regression: on a >= 400 response after body_iter has
    yielded multiple frames, the recovery branch must drain whatever is
    still on `_outbound` AND prepend `last_inflight`, so every frame
    re-appears in order on the next attempt. Pre-fix the branch only
    prepended last_inflight, silently dropping frames yielded earlier."""
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    e1 = {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}
    e2 = {"type": "TEXT_MESSAGE_END"}
    e3 = {"type": "RUN_FINISHED"}
    # Three frames queued upfront. Attempt 1 advances body_iter twice
    # (yielding e1 and e2) before the backend returns 503. With the
    # pre-fix code, only e2 would have been preserved (as last_inflight)
    # and e1 would be irrecoverable on attempt 2. Post-fix attempt 2
    # must yield [e1, e2, e3] in that order — and ONLY once each.
    await chan._outbound.put(e1)
    await chan._outbound.put(e2)
    await chan._outbound.put(e3)

    first: list[bytes] = []
    second: list[bytes] = []
    chan._http = _MixedHttp(  # type: ignore[assignment]
        attempts=[
            {
                "mode": "bad_status",
                "advance": 2,
                "collected": first,
                "status_code": 503,
            },
            {
                "mode": "ok",
                "collected": second,
                "terminate": True,
            },
        ],
        chan=chan,
    )

    real_sleep = asyncio.sleep

    async def _instant_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr("runner.channel.asyncio.sleep", _instant_sleep)

    await asyncio.wait_for(chan._stream_outbound(), timeout=5.0)

    # Attempt 1 yielded exactly two frames before the 503.
    assert _decode_lines(first) == [e1, e2]
    # Attempt 2: pre-fix would be [e2, e3] (e1 gone). Post-fix preserves
    # all three frames in order.
    decoded = _decode_lines(second)
    assert decoded == [e1, e2, e3], (
        f"4xx recovery lost frames; expected [e1,e2,e3] got "
        f"{[d.get('type') for d in decoded]}"
    )


async def test_4xx_then_sentinel_does_not_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `None` sentinel encountered while draining the queue inside the
    4xx recovery branch must terminate the stream loop, not retry."""
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    e1 = {"type": "TEXT_MESSAGE_CONTENT", "delta": "hi"}
    await chan._outbound.put(e1)
    await chan._outbound.put(None)  # graceful close requested

    first: list[bytes] = []
    chan._http = _MixedHttp(  # type: ignore[assignment]
        attempts=[
            {
                "mode": "bad_status",
                "advance": 1,
                "collected": first,
                "status_code": 503,
            },
        ],
        chan=chan,
    )

    real_sleep = asyncio.sleep

    async def _instant_sleep(_):
        await real_sleep(0)

    monkeypatch.setattr("runner.channel.asyncio.sleep", _instant_sleep)

    # If the loop didn't honour the sentinel during drain it would call
    # `_http.stream` a second time and trigger _MixedHttp's
    # AssertionError. The test passes when the call returns cleanly.
    await asyncio.wait_for(chan._stream_outbound(), timeout=5.0)
    assert _decode_lines(first) == [e1]


async def test_reconnect_preserves_order_when_inflight_and_queue_both_present() -> None:
    """When the transport raises after the first frame, the second
    attempt's body must yield in order: in-flight frame (E1), then the
    drained queue (E2, E3). No frame is dropped or reordered."""
    from runner.channel import BackendChannel

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )
    e1 = {"type": "E1"}
    e2 = {"type": "E2"}
    e3 = {"type": "E3"}
    await chan._outbound.put(e1)
    await chan._outbound.put(e2)
    await chan._outbound.put(e3)

    first: list[bytes] = []
    second: list[bytes] = []
    chan._http = _FakeHttp(
        attempts=[
            {"raise_after": 1, "collected": first},
            {"raise_after": None, "collected": second, "terminate": True},
        ],
        chan=chan,
    )

    import runner.channel as _channel_mod

    real_sleep = asyncio.sleep

    async def _instant_sleep(_):
        await real_sleep(0)

    orig_sleep = _channel_mod.asyncio.sleep
    _channel_mod.asyncio.sleep = _instant_sleep  # type: ignore[assignment]
    try:
        await asyncio.wait_for(chan._stream_outbound(), timeout=5.0)
    finally:
        _channel_mod.asyncio.sleep = orig_sleep  # type: ignore[assignment]

    assert _decode_lines(first) == [e1]
    assert _decode_lines(second) == [e1, e2, e3]
