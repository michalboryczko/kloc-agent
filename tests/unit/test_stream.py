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
