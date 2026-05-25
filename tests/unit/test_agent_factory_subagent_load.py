"""End-to-end wiring of the subagent autoregistry into `build_agent`.

Drives `runner.agent_factory.build_agent` against a tmp agents
directory containing a single valid AGENT.md and asserts the returned
seed Agent's tool registry carries the subagent under its frontmatter
`name`."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


def _write_agent(tmp_path: Path, name: str) -> None:
    agent_dir = tmp_path / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "AGENT.md").write_text(
        f'---\n'
        f'name: {name}\n'
        f'description: "Test sub-agent named {name}."\n'
        f'---\n\n'
        f"You are the {name} sub-agent.\n",
        encoding="utf-8",
    )


def _build_payload(agents_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="s",
        run_id="r",
        runner_id="ri",
        runner_secret="x",
        system_prompt="base prompt",
        model_id="",
        llm_provider="gemini",
        prior_messages=[],
        state={},
        mcp_endpoints=[],
        skills_dir="/nonexistent-skills-dir",
        agents_dir=str(agents_dir),
        backend_url="http://localhost",
        heartbeat_interval_s=15,
        pg_dsn="",
        inbox_queue="",
    )


def _stub_audit_sender() -> Any:
    sender = MagicMock()
    sender._build_payload = MagicMock(return_value={})

    async def _post(payload: Any, name: str) -> dict:
        return {}

    sender._post = _post
    return sender


def test_build_agent_registers_autoregistered_subagent_as_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runner import agent_factory

    _write_agent(tmp_path, "alpha")

    # Stub model_factory.create_model so we don't need a real provider.
    monkeypatch.setattr(
        agent_factory, "create_model", lambda **kw: MagicMock(name="model")
    )

    payload = _build_payload(tmp_path)
    audit_sender = _stub_audit_sender()

    agent, _provider = agent_factory.build_agent(
        payload, mcp_tools=[], audit_sender=audit_sender
    )

    registered = set(agent.tool_registry.registry.keys())
    assert "alpha" in registered, (
        f"expected 'alpha' subagent in tool registry; got {sorted(registered)}"
    )


def test_build_agent_omits_summarizer_when_not_present_in_agents_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runner import agent_factory

    # Only `alpha` lives in tmp_path — `summarizer` is intentionally absent.
    _write_agent(tmp_path, "alpha")

    monkeypatch.setattr(
        agent_factory, "create_model", lambda **kw: MagicMock(name="model")
    )

    payload = _build_payload(tmp_path)
    audit_sender = _stub_audit_sender()

    agent, _provider = agent_factory.build_agent(
        payload, mcp_tools=[], audit_sender=audit_sender
    )

    registered = set(agent.tool_registry.registry.keys())
    assert "summarizer" not in registered, (
        "summarizer must not be present unless tmp agents dir defines it; "
        "the hardcoded summarizer block in agent_factory.py is gone"
    )
