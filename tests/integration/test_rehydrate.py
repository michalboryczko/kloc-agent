"""Integration tests for the hydration payload write + mount paths (Track D, D4).

No DB required — these exercise `hydrate.py` directly (write tempfile,
parse via Pydantic, build mount config). AC16.
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from src.db.models import HydrationPayload, McpStdioEndpoint
from src.runner_mgmt.hydrate import (
    HYDRATION_RUNNER_DIR,
    HYDRATION_VOLUME_NAME,
    build_hydration_mount,
    build_skills_mount,
    cleanup_hydration_tempfile,
    runner_mount_path_for,
    write_hydration_tempfile,
)


pytestmark = pytest.mark.integration


def _make_payload(session_id: str, prior_messages: list[dict]) -> HydrationPayload:
    return HydrationPayload(
        session_id=session_id,
        run_id=str(uuid.uuid4()),
        runner_id=str(uuid.uuid4()),
        runner_secret="test-secret-no-prod",
        system_prompt="You are a test agent.",
        model_id="gemini-3.1-pro-preview",
        llm_provider="gemini",
        prior_messages=prior_messages,
        state={},
        mcp_endpoints=[
            McpStdioEndpoint(
                command="uv",
                args=["run", "kloc-intelligence", "mcp-server"],
            )
        ],
        skills_dir="/skills",
        backend_url="http://backend:8000",
        heartbeat_interval_s=15,
        pg_dsn="postgresql+asyncpg://kloc:changeme@postgres:5432/kloc_agent",
        inbox_queue=f"inbox_{session_id.replace('-', '')}",
    )


def test_write_hydration_tempfile_round_trip(tmp_path, monkeypatch):
    """write_hydration_tempfile produces a JSON that parses back to identical payload (AC16)."""
    monkeypatch.setattr(
        "src.runner_mgmt.hydrate._backend_path_for",
        lambda rid: tmp_path / f"{rid}.json",
    )

    sess = "00000000-0000-0000-0000-000000000001"
    payload = _make_payload(
        session_id=sess,
        prior_messages=[
            {"id": "m1", "role": "user", "content": "hello"},
            {"id": "m2", "role": "assistant", "content": "hi back"},
        ],
    )

    path = write_hydration_tempfile(payload.runner_id, payload)
    assert path.is_file()

    body = json.loads(path.read_text())
    reparsed = HydrationPayload.model_validate(body)
    assert reparsed.session_id == sess
    assert len(reparsed.prior_messages) == 2
    assert reparsed.mcp_endpoints[0].command == "uv"
    assert reparsed.backend_url == "http://backend:8000"
    assert reparsed.skills_dir == "/skills"


def test_write_hydration_includes_full_prior_messages(tmp_path, monkeypatch):
    """All 7 prior messages (full DB history) make it into the tempfile."""
    monkeypatch.setattr(
        "src.runner_mgmt.hydrate._backend_path_for",
        lambda rid: tmp_path / f"{rid}.json",
    )

    history = [
        {"id": f"m{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"turn-{i}"}
        for i in range(7)
    ]
    payload = _make_payload(
        session_id="00000000-0000-0000-0000-000000000002",
        prior_messages=history,
    )

    path = write_hydration_tempfile(payload.runner_id, payload)
    parsed = HydrationPayload.model_validate_json(path.read_text())
    assert [m["id"] for m in parsed.prior_messages] == [f"m{i}" for i in range(7)]


def test_write_hydration_accepts_plain_dict(tmp_path, monkeypatch):
    """write_hydration_tempfile accepts a dict (not just Pydantic) — exercised by tests / scripts."""
    monkeypatch.setattr(
        "src.runner_mgmt.hydrate._backend_path_for",
        lambda rid: tmp_path / f"{rid}.json",
    )

    raw = {"session_id": "abc", "extras": {"k": 1}}
    path = write_hydration_tempfile("rid-1", raw)
    assert json.loads(path.read_text()) == raw


def test_cleanup_hydration_tempfile_removes_file(tmp_path, monkeypatch):
    """terminate() path calls cleanup; file is gone (Contract D §549)."""
    monkeypatch.setattr(
        "src.runner_mgmt.hydrate._backend_path_for",
        lambda rid: tmp_path / f"{rid}.json",
    )

    payload = _make_payload(
        session_id="00000000-0000-0000-0000-000000000003",
        prior_messages=[],
    )
    path = write_hydration_tempfile(payload.runner_id, payload)
    assert path.is_file()

    cleanup_hydration_tempfile(payload.runner_id)
    assert not path.exists()


def test_cleanup_hydration_tempfile_idempotent(tmp_path, monkeypatch):
    """cleanup is safe to call twice."""
    monkeypatch.setattr(
        "src.runner_mgmt.hydrate._backend_path_for",
        lambda rid: tmp_path / f"{rid}.json",
    )
    cleanup_hydration_tempfile("nonexistent-runner-id")
    cleanup_hydration_tempfile("nonexistent-runner-id")  # second call must not raise


def test_build_hydration_mount_is_readonly_named_volume():
    """Mount config is a named volume mounted RO at /run/kloc (Contract D §546, post-B-INFRA-3)."""
    mount = build_hydration_mount()
    assert mount["Type"] == "volume"
    assert mount["Source"] == HYDRATION_VOLUME_NAME
    assert mount["Target"] == HYDRATION_RUNNER_DIR
    assert mount["Target"] == "/run/kloc"
    assert mount["ReadOnly"] is True


def test_runner_mount_path_for_returns_per_runner_file():
    """Per-runner-id hydration path is `/run/kloc/<rid>.json` — exported as KLOC_HYDRATION_PATH."""
    assert runner_mount_path_for("abc-123") == "/run/kloc/abc-123.json"


def test_build_skills_mount_is_readonly_named_volume():
    """Skills mount is the `kloc-skills` named volume, read-only at /skills (post-B-INFRA-3 audit point 1)."""
    mount = build_skills_mount(str(Path(__file__).parent))
    assert mount["Type"] == "volume"
    assert mount["Source"] == "kloc-skills"
    assert mount["Target"] == "/skills"
    assert mount["ReadOnly"] is True


def test_hydration_payload_sample_fixture_parses(hydration_payload_sample):
    """The shared JSON fixture round-trips through HydrationPayload."""
    parsed = HydrationPayload.model_validate(hydration_payload_sample)
    assert parsed.llm_provider == "gemini"
    assert parsed.skills_dir == "/skills"
    assert parsed.mcp_endpoints[0].command == "uv"
    assert parsed.model_id == "gemini-3.1-pro-preview"


def test_hydration_payload_rejects_invalid_llm_provider():
    """LLM provider Literal enforced — typo surfaces as a Pydantic ValidationError."""
    from pydantic import ValidationError

    good = _make_payload(
        session_id="00000000-0000-0000-0000-000000000004",
        prior_messages=[],
    ).model_dump()
    good["llm_provider"] = "openai-deprecated"

    with pytest.raises(ValidationError):
        HydrationPayload.model_validate(good)


def test_hydration_tempfile_chmod_0o600(tmp_path, monkeypatch):
    """Tempfile is chmod 600 (no world-readable secrets) — Issue not strictly an AC but a security check."""
    monkeypatch.setattr(
        "src.runner_mgmt.hydrate._backend_path_for",
        lambda rid: tmp_path / f"{rid}.json",
    )
    payload = _make_payload(
        session_id="00000000-0000-0000-0000-000000000005",
        prior_messages=[],
    )
    path = write_hydration_tempfile(payload.runner_id, payload)
    mode = oct(os.stat(path).st_mode & 0o777)
    assert mode == "0o600", f"expected 0o600, got {mode}"
