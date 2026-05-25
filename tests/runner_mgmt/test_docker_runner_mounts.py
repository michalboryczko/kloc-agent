"""Verify the runner's `HostConfig.Mounts` list shape.

The runner sees four mounts in a fixed order: hydration, skills,
agents, projects. The mounts are read-only named volumes; the projects
volume is the surface the `read_project_file` Strands tool consumes.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _FakeContainer:
    def __init__(self) -> None:
        self.id = "fake-container-id"

    async def start(self) -> None:
        return None


class _FakeContainers:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, *, config: dict, name: str) -> _FakeContainer:
        self.created.append({"config": config, "name": name})
        return _FakeContainer()


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _FakeContainers()

    async def close(self) -> None:
        return None


@pytest.fixture
def fake_docker(monkeypatch) -> _FakeDocker:
    from src.runner_mgmt import docker_runner

    fake = _FakeDocker()

    class _Stub:
        @staticmethod
        def Docker() -> _FakeDocker:
            return fake

    monkeypatch.setattr(docker_runner, "aiodocker", _Stub)
    return fake


def _payload() -> dict[str, Any]:
    return {
        "session_id": "sess-1",
        "runner_id": "rid-1",
        "run_id": "run-1",
        "runner_secret": "secret-xyz",
    }


def test_spawn_emits_four_mounts_in_expected_order(
    fake_docker, monkeypatch, tmp_path
) -> None:
    from src.runner_mgmt import docker_runner

    monkeypatch.setenv("KLOC_HYDRATION_BACKEND_DIR", str(tmp_path))
    monkeypatch.setenv("KLOC_HYDRATION_VOLUME", "kloc-hydration")
    monkeypatch.setenv("KLOC_SKILLS_VOLUME", "kloc-skills")
    monkeypatch.setenv("KLOC_AGENTS_VOLUME", "kloc-agents")
    monkeypatch.setenv("KLOC_PROJECTS_VOLUME", "kloc-projects")

    runner = docker_runner.DockerRunner(
        image="kloc-agent-runner:dev",
        network="kloc",
        backend_url="http://backend:8000",
        skills_host_dir="./skills",
        agents_host_dir="./agents",
    )

    asyncio.run(runner.spawn(_payload()))
    created = fake_docker.containers.created
    assert len(created) == 1
    mounts = created[0]["config"]["HostConfig"]["Mounts"]
    assert len(mounts) == 4

    expected = [
        ("kloc-hydration", "/run/kloc"),
        ("kloc-skills", "/skills"),
        ("kloc-agents", "/agents"),
        ("kloc-projects", "/projects"),
    ]
    actual = [(m["Source"], m["Target"]) for m in mounts]
    assert actual == expected
    for m in mounts:
        assert m["Type"] == "volume"
        assert m["ReadOnly"] is True
