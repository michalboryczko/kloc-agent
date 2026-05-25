"""Tests for `runner/agents_loader.py`.

Covers the discovery + spec parse path (sorted output, skip-on-invalid,
duplicate-name handling, missing-dir tolerance, name-regex validation)
and the construction path (one Strands Agent per spec, MCP-collision
skip with audit emit)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _agent_md(name: str, description: str = "A test sub-agent.") -> str:
    return (
        f"---\n"
        f"name: {name}\n"
        f'description: "{description}"\n'
        f"---\n\n"
        f"System prompt body for {name}.\n"
    )


def test_discover_agents_returns_sorted_specs(tmp_path: Path) -> None:
    from runner.agents_loader import discover_agents

    _write(tmp_path / "beta" / "AGENT.md", _agent_md("beta"))
    _write(tmp_path / "alpha" / "AGENT.md", _agent_md("alpha"))

    specs = discover_agents(tmp_path)

    assert [s.name for s in specs] == ["alpha", "beta"]
    assert all(s.system_prompt.startswith("System prompt body for") for s in specs)


def test_discover_agents_skips_invalid_frontmatter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from runner.agents_loader import discover_agents

    _write(tmp_path / "good" / "AGENT.md", _agent_md("good"))
    _write(
        tmp_path / "bad" / "AGENT.md",
        '---\ndescription: "missing name field"\n---\nbody\n',
    )

    with caplog.at_level(logging.WARNING):
        specs = discover_agents(tmp_path)

    assert [s.name for s in specs] == ["good"]
    assert any("agents.invalid_spec" in r.message for r in caplog.records)


def test_discover_agents_skips_duplicate_name(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from runner.agents_loader import discover_agents

    _write(tmp_path / "a" / "AGENT.md", _agent_md("dup"))
    _write(tmp_path / "b" / "AGENT.md", _agent_md("dup"))

    with caplog.at_level(logging.WARNING):
        specs = discover_agents(tmp_path)

    assert len(specs) == 1
    assert any("agents.duplicate_name" in r.message for r in caplog.records)


def test_discover_agents_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    from runner.agents_loader import discover_agents

    missing = tmp_path / "does-not-exist"
    assert discover_agents(missing) == []


def test_discover_agents_invalid_name_regex(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from runner.agents_loader import discover_agents

    _write(tmp_path / "bad" / "AGENT.md", _agent_md("Bad Name"))

    with caplog.at_level(logging.WARNING):
        specs = discover_agents(tmp_path)

    assert specs == []
    assert any("agents.invalid_spec" in r.message for r in caplog.records)


def test_build_subagents_constructs_strands_agent_per_spec(
    tmp_path: Path,
) -> None:
    from runner.agents_loader import build_subagents, discover_agents

    _write(tmp_path / "alpha" / "AGENT.md", _agent_md("alpha"))
    _write(tmp_path / "beta" / "AGENT.md", _agent_md("beta"))
    specs = discover_agents(tmp_path)

    sentinel_model = object()
    sentinel_tools = [object(), object()]

    with patch("strands.Agent") as mock_agent_cls:
        mock_agent_cls.side_effect = lambda **kw: ("built", kw)
        subagents = build_subagents(
            specs,
            model=sentinel_model,
            tools=sentinel_tools,
            reserved_names=set(),
            audit_emit=None,
        )

    assert len(subagents) == 2
    assert mock_agent_cls.call_count == 2
    for call, expected_name in zip(mock_agent_cls.call_args_list, ["alpha", "beta"]):
        kwargs = call.kwargs
        assert kwargs["model"] is sentinel_model
        assert kwargs["name"] == expected_name
        assert kwargs["tools"] is sentinel_tools
        assert "System prompt body for" in kwargs["system_prompt"]


def test_build_subagents_skips_name_in_reserved_set(tmp_path: Path) -> None:
    from runner.agents_loader import build_subagents, discover_agents

    _write(tmp_path / "summarizer" / "AGENT.md", _agent_md("summarizer"))
    _write(tmp_path / "second" / "AGENT.md", _agent_md("second"))
    specs = discover_agents(tmp_path)

    emitted: list[dict[str, Any]] = []

    async def _audit_emit(payload: dict) -> None:
        emitted.append(payload)

    with patch("strands.Agent") as mock_agent_cls:
        mock_agent_cls.side_effect = lambda **kw: ("built", kw["name"])
        subagents = build_subagents(
            specs,
            model=object(),
            tools=[],
            reserved_names={"summarizer"},
            audit_emit=_audit_emit,
        )

    # `second` is constructed; `summarizer` is skipped.
    assert len(subagents) == 1
    assert mock_agent_cls.call_count == 1
    assert mock_agent_cls.call_args.kwargs["name"] == "second"

    assert len(emitted) == 1
    assert emitted[0]["reason"] == "name_conflicts_with_mcp_tool"
    assert emitted[0]["file"].endswith("summarizer/AGENT.md")
