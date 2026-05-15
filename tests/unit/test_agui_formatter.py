"""Unit tests for the AG-UI event formatter normalizer."""
from __future__ import annotations

import pytest

from src.streaming.agui_event_formatter import (
    is_run_lifecycle_terminal,
    normalize,
)

pytestmark = pytest.mark.unit


def test_normalize_dict_stamps_timestamp():
    out = normalize({"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": "a"})
    assert out["type"] == "TEXT_MESSAGE_CONTENT"
    assert out["delta"] == "a"
    assert "timestamp" in out


def test_normalize_preserves_existing_timestamp():
    out = normalize({"type": "RUN_STARTED", "threadId": "t", "runId": "r", "timestamp": 99})
    assert out["timestamp"] == 99


def test_is_run_lifecycle_terminal_finished():
    assert is_run_lifecycle_terminal({"type": "RUN_FINISHED"}) is True


def test_is_run_lifecycle_terminal_error():
    assert is_run_lifecycle_terminal({"type": "RUN_ERROR"}) is True


def test_is_run_lifecycle_terminal_other():
    assert is_run_lifecycle_terminal({"type": "TEXT_MESSAGE_END"}) is False
