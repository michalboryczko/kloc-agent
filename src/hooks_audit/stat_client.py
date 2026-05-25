"""Best-effort file-stat client used by the BeforeToolCall policy layer.

Single 500 ms attempt. Any failure (timeout, connection error, non-2xx)
logs a warning, increments the `kloc_agent.policy.stat_unavailable_total`
OTel counter, and returns `None`. The caller treats `None` as
`allow` — the cap is guidance, not a security boundary.
"""
from __future__ import annotations

import logging
import time
from typing import TypedDict

import httpx

from src.settings import get_settings


log = logging.getLogger(__name__)

STAT_TIMEOUT_S = 0.5


class FileStat(TypedDict):
    exists: bool
    size_bytes: int
    is_file: bool


try:
    from opentelemetry import metrics as _otel_metrics

    _meter = _otel_metrics.get_meter("kloc_agent.policy")
    _stat_unavailable_counter = _meter.create_counter(
        "kloc_agent.policy.stat_unavailable_total",
        description="Stat-call failures from the BeforeToolCall policy layer.",
    )
    _stat_latency_histogram = _meter.create_histogram(
        "kloc_agent.policy.stat_latency_ms",
        description="Round-trip latency of the file-stat HTTP call.",
        unit="ms",
    )
except Exception:  # pragma: no cover - OTel absence must not crash boot
    _stat_unavailable_counter = None
    _stat_latency_histogram = None


_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=STAT_TIMEOUT_S)
    return _client


def _record_unavailable() -> None:
    if _stat_unavailable_counter is None:
        return
    try:
        _stat_unavailable_counter.add(1)
    except Exception:
        log.exception("stat_unavailable_counter_failed")


def _record_latency(elapsed_ms: float) -> None:
    if _stat_latency_histogram is None:
        return
    try:
        _stat_latency_histogram.record(elapsed_ms)
    except Exception:
        log.exception("stat_latency_histogram_failed")


async def stat(path: str, *, base_url: str | None = None) -> FileStat | None:
    """Return the file-stat shape for `path`, or `None` on any failure."""
    url = base_url or get_settings().intelligence_stat_url
    started = time.perf_counter()
    try:
        client = _get_client()
        response = await client.get(url, params={"path": path})
    except Exception as exc:
        log.warning("policy.stat_unavailable url=%s err=%r", url, exc)
        _record_unavailable()
        return None
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    _record_latency(elapsed_ms)
    if response.status_code == 404:
        return {"exists": False, "size_bytes": 0, "is_file": False}
    if response.status_code >= 400:
        log.warning(
            "policy.stat_unavailable url=%s status=%d",
            url,
            response.status_code,
        )
        _record_unavailable()
        return None
    try:
        body = response.json()
    except Exception as exc:
        log.warning("policy.stat_bad_body url=%s err=%r", url, exc)
        _record_unavailable()
        return None
    return {
        "exists": bool(body.get("exists", False)),
        "size_bytes": int(body.get("size_bytes", 0)),
        "is_file": bool(body.get("is_file", False)),
    }


async def _aclose_for_tests() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
