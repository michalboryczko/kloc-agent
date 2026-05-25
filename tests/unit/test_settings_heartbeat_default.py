"""Pinned default: `runner_heartbeat_timeout_s` is 60 s.

The cold-start MCP-init handshake plus Strands+skills build runs in the
15–40 s range on a moderately loaded host; a 30 s default leaves no
margin. Guards against an accidental revert.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.unit


def test_runner_heartbeat_timeout_default_is_60s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUNNER_HEARTBEAT_TIMEOUT_S", raising=False)
    monkeypatch.setenv("KLOC_STUB_MODE", "true")

    from src.settings import Settings

    assert Settings().runner_heartbeat_timeout_s == 60
