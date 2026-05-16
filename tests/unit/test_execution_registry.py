"""Tests for `src/streaming/execution_registry.py`.

Regression: `is_evictable` used a bare `assert self.finished_ts is not None`
which is stripped under `python -O`, leaving the next line to raise
`TypeError` on `None`. Fixed to raise `RuntimeError` explicitly.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_is_evictable_running_returns_false() -> None:
    from src.streaming.execution_registry import Execution

    ex = Execution(session_id="s1", run_id="r1")
    assert ex.is_evictable() is False


def test_is_evictable_raises_runtimeerror_when_invariant_violated() -> None:
    from src.streaming.execution_registry import Execution

    ex = Execution(session_id="s1", run_id="r1")
    ex.status = "finished"
    ex.finished_ts = None
    with pytest.raises(RuntimeError, match="finished_ts is None"):
        ex.is_evictable()


def test_is_evictable_finished_within_ttl_returns_false() -> None:
    import time

    from src.streaming.execution_registry import Execution

    ex = Execution(session_id="s1", run_id="r1")
    ex.status = "finished"
    ex.finished_ts = time.monotonic()
    assert ex.is_evictable() is False
