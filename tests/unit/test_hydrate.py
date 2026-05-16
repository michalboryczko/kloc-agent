"""Tests for `src/runner_mgmt/hydrate.py`.

Regression: module-level `HYDRATION_BACKEND_DIR` / `HYDRATION_VOLUME_NAME`
were read from `os.environ` at import time, so tests / tooling that set
those env vars after the module loaded saw the old values. Fix: defer
to call-site reads.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.unit


def test_volume_name_read_at_call_time(monkeypatch) -> None:
    from src.runner_mgmt import hydrate

    monkeypatch.setenv("KLOC_HYDRATION_VOLUME", "test-volume-x")
    m = hydrate.build_hydration_mount()
    assert m["Source"] == "test-volume-x"


def test_backend_dir_read_at_call_time(monkeypatch, tmp_path) -> None:
    from src.runner_mgmt import hydrate

    monkeypatch.setenv("KLOC_HYDRATION_BACKEND_DIR", str(tmp_path))
    path = hydrate.write_hydration_tempfile("rid-test", {"hello": "world"})
    try:
        assert path.parent == tmp_path
        assert path.name == "rid-test.json"
        assert json.loads(path.read_text()) == {"hello": "world"}
    finally:
        path.unlink(missing_ok=True)


def test_cleanup_removes_per_runner_file(monkeypatch, tmp_path) -> None:
    from src.runner_mgmt import hydrate

    monkeypatch.setenv("KLOC_HYDRATION_BACKEND_DIR", str(tmp_path))
    path = hydrate.write_hydration_tempfile("rid-cleanup", {"x": 1})
    assert path.exists()
    hydrate.cleanup_hydration_tempfile("rid-cleanup")
    assert not path.exists()
