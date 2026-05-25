"""`BackendChannel._heartbeat_loop` must emit the first beat at t≈0.

The previous implementation slept `HEARTBEAT_INTERVAL_S` (15 s) before
the first emit, which combined with a slow MCP-init could push the
runner's first explicit beat past the backend watcher's timeout. This
test asserts the inversion: emit-then-sleep.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


async def test_first_heartbeat_is_immediate_then_periodic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runner import channel as _channel
    from runner.channel import BackendChannel

    monkeypatch.setattr(_channel, "HEARTBEAT_INTERVAL_S", 0.1)

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )

    task = asyncio.create_task(chan._heartbeat_loop())
    try:
        first = await asyncio.wait_for(chan._outbound.get(), timeout=0.5)
        assert first.get("type") == "heartbeat", (
            f"expected the first dequeued frame to be a heartbeat, "
            f"got {first!r}"
        )

        second = await asyncio.wait_for(chan._outbound.get(), timeout=1.0)
        assert second.get("type") == "heartbeat", (
            f"expected a second heartbeat after the interval, got {second!r}"
        )
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_first_heartbeat_arrives_before_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runner import channel as _channel
    from runner.channel import BackendChannel

    monkeypatch.setattr(_channel, "HEARTBEAT_INTERVAL_S", 5.0)

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run1",
    )

    task = asyncio.create_task(chan._heartbeat_loop())
    try:
        first = await asyncio.wait_for(chan._outbound.get(), timeout=0.1)
        assert first.get("type") == "heartbeat", (
            f"first frame must be a heartbeat at t≈0; got {first!r}"
        )
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
