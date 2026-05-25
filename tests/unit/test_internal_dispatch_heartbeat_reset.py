"""The ingress' heartbeat-reset frame set must include `runner_ready`.

`runner_ready` is the runner's first emitted frame and is the de-facto
cold-start beat. Recognising it at the ingress closes the spawn → first
explicit heartbeat gap that would otherwise outrun the watcher under
moderate-load MCP-init.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, dict]] = []

    async def publish(self, sid: str, rid: str, frame: dict) -> None:
        self.published.append((sid, rid, frame))


class _StubRegistry:
    def __init__(self) -> None:
        self.beats: list[str] = []

    async def on_heartbeat_frame(self, session_id: str) -> None:
        self.beats.append(session_id)


def _make_request(registry: _StubRegistry | None = None) -> tuple[object, _FakeBus, _StubRegistry]:
    bus = _FakeBus()
    reg = registry if registry is not None else _StubRegistry()

    class _State:
        runner_registry = reg
        event_bus = bus
        active_run_by_session: dict[str, str] = {}
        pending_pre_run_started: dict[str, list[dict]] = {}

    class _App:
        state = _State()

    class _Request:
        app = _App()

    return _Request(), bus, reg


async def test_runner_ready_resets_watcher_and_skips_bus() -> None:
    from src.api.internal import _dispatch_frame

    req, bus, reg = _make_request()
    await _dispatch_frame(req, "s1", {"type": "runner_ready"})

    assert reg.beats == ["s1"], (
        f"expected on_heartbeat_frame to be called once for runner_ready, "
        f"got {reg.beats!r}"
    )
    assert bus.published == [], (
        "runner_ready must not be published to the AG-UI event bus"
    )


async def test_heartbeat_and_runner_heartbeat_still_reset_watcher() -> None:
    from src.api.internal import _dispatch_frame

    req, bus, reg = _make_request()
    await _dispatch_frame(req, "s1", {"type": "heartbeat"})
    await _dispatch_frame(req, "s1", {"type": "RunnerHeartbeat"})

    assert reg.beats == ["s1", "s1"]
    assert bus.published == []


async def test_non_beat_frame_does_not_reset_watcher() -> None:
    from src.api.internal import _dispatch_frame

    req, _bus, reg = _make_request()
    await _dispatch_frame(
        req, "s1", {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"}
    )

    assert reg.beats == [], (
        f"on_heartbeat_frame must not fire on AG-UI events; got {reg.beats!r}"
    )


async def test_first_beat_records_spawn_to_first_beat_histogram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import internal

    recorded: list[float] = []

    class _Hist:
        def record(self, value: float) -> None:
            recorded.append(value)

    monkeypatch.setattr(internal, "_spawn_to_first_beat_hist", _Hist())
    monkeypatch.setattr(internal, "_runner_spawn_ts", {})
    monkeypatch.setattr(internal, "_first_beat_recorded", set())

    internal.record_runner_spawned("s-hist")

    req, _bus, _reg = _make_request()
    await internal._dispatch_frame(req, "s-hist", {"type": "runner_ready"})
    await internal._dispatch_frame(req, "s-hist", {"type": "heartbeat"})

    assert len(recorded) == 1, (
        f"histogram must record exactly one observation per session, "
        f"got {recorded!r}"
    )
    assert recorded[0] >= 0.0


async def test_histogram_records_nothing_without_spawn_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.api import internal

    recorded: list[float] = []

    class _Hist:
        def record(self, value: float) -> None:
            recorded.append(value)

    monkeypatch.setattr(internal, "_spawn_to_first_beat_hist", _Hist())
    monkeypatch.setattr(internal, "_runner_spawn_ts", {})
    monkeypatch.setattr(internal, "_first_beat_recorded", set())

    req, _bus, _reg = _make_request()
    await internal._dispatch_frame(req, "s-orphan", {"type": "runner_ready"})

    assert recorded == [], (
        "histogram must not record when spawn timestamp was never stored"
    )
