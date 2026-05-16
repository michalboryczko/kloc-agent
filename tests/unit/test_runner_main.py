"""Tests for `runner/__main__.py:_read_hydration`.

Regression: `json.load(f)` was unbounded — a malformed or malicious
hydration file could OOM or hang the runner. Fix: cap at
`MAX_HYDRATION_BYTES` and refuse anything larger.
"""
from __future__ import annotations

import json
import os

import pytest

pytestmark = pytest.mark.unit


def test_oversize_hydration_file_raises(tmp_path, monkeypatch) -> None:
    from runner import __main__ as runner_main

    big = tmp_path / "hydration.json"
    big.write_bytes(b"x" * (runner_main.MAX_HYDRATION_BYTES + 1))
    monkeypatch.setenv("KLOC_HYDRATION_PATH", str(big))

    with pytest.raises(RuntimeError, match="exceeds"):
        runner_main._read_hydration()


def test_well_formed_small_hydration_loads(tmp_path, monkeypatch) -> None:
    from runner import __main__ as runner_main

    p = tmp_path / "hydration.json"
    payload = {"session_id": "s1", "runner_id": "r1"}
    p.write_text(json.dumps(payload))
    monkeypatch.setenv("KLOC_HYDRATION_PATH", str(p))

    assert runner_main._read_hydration() == payload
