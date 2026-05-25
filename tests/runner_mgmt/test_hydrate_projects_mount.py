"""Tests for `build_projects_mount` in `src/runner_mgmt/hydrate.py`.

The mount-builder is a thin wrapper over a lazy env-read so an operator
override applied after module import is honoured — mirrors the existing
`build_skills_mount` / `build_agents_mount` shape.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_projects_mount_default_shape(monkeypatch) -> None:
    from src.runner_mgmt import hydrate

    monkeypatch.delenv("KLOC_PROJECTS_VOLUME", raising=False)
    m = hydrate.build_projects_mount()
    assert m == {
        "Type": "volume",
        "Source": "kloc-projects",
        "Target": "/projects",
        "ReadOnly": True,
    }


def test_projects_mount_env_override(monkeypatch) -> None:
    from src.runner_mgmt import hydrate

    monkeypatch.setenv("KLOC_PROJECTS_VOLUME", "foo")
    m = hydrate.build_projects_mount()
    assert m["Source"] == "foo"
    assert m["Target"] == "/projects"
    assert m["ReadOnly"] is True
    assert m["Type"] == "volume"
