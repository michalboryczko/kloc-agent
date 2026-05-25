"""Stat-client behaviour: best-effort with metric side-effects.

A timeout returns `None` and increments the
`kloc_agent.policy.stat_unavailable_total` counter (verified by
patching the module-level counter handle). A 200 returns the parsed
TypedDict. A 404 returns `{exists: false, size_bytes: 0, is_file: false}`.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.hooks_audit import stat_client


pytestmark = pytest.mark.unit


class _CounterStub:
    def __init__(self) -> None:
        self.adds: list[tuple[int, dict[str, Any] | None]] = []

    def add(self, value: int, attributes: dict[str, Any] | None = None) -> None:
        self.adds.append((value, attributes))


@pytest.fixture
def reset_client():
    yield
    stat_client._client = None


@pytest.fixture
def stub_counter(monkeypatch):
    counter = _CounterStub()
    monkeypatch.setattr(stat_client, "_stat_unavailable_counter", counter)
    return counter


async def _install_fake_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    fake = httpx.AsyncClient(transport=transport, timeout=stat_client.STAT_TIMEOUT_S)
    monkeypatch.setattr(stat_client, "_get_client", lambda: fake)


async def test_stat_timeout_returns_none_and_increments_counter(
    monkeypatch, stub_counter, reset_client
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout")

    await _install_fake_client(monkeypatch, _handler)
    result = await stat_client.stat("/workspace/big.json", base_url="http://x/v1/file_stat")
    assert result is None
    assert len(stub_counter.adds) == 1


async def test_stat_connection_error_returns_none_and_increments(
    monkeypatch, stub_counter, reset_client
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connect error")

    await _install_fake_client(monkeypatch, _handler)
    result = await stat_client.stat("/workspace/x", base_url="http://x/v1/file_stat")
    assert result is None
    assert len(stub_counter.adds) == 1


async def test_stat_200_returns_parsed_shape(
    monkeypatch, stub_counter, reset_client
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"exists": True, "size_bytes": 5_242_880, "is_file": True}
        )

    await _install_fake_client(monkeypatch, _handler)
    result = await stat_client.stat("/workspace/x", base_url="http://x/v1/file_stat")
    assert result == {"exists": True, "size_bytes": 5_242_880, "is_file": True}
    assert stub_counter.adds == []


async def test_stat_404_returns_not_exists(
    monkeypatch, stub_counter, reset_client
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    await _install_fake_client(monkeypatch, _handler)
    result = await stat_client.stat("/workspace/missing", base_url="http://x/v1/file_stat")
    assert result == {"exists": False, "size_bytes": 0, "is_file": False}
    assert stub_counter.adds == []


async def test_stat_500_returns_none_and_increments(
    monkeypatch, stub_counter, reset_client
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    await _install_fake_client(monkeypatch, _handler)
    result = await stat_client.stat("/workspace/x", base_url="http://x/v1/file_stat")
    assert result is None
    assert len(stub_counter.adds) == 1
