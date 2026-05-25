"""Argument-aware policy verification.

Each test injects a `Settings` with a tailored `tool_limits` and
patches the stat client where the file_read evaluator would otherwise
hit a real HTTP endpoint. The deny-set fallback retains lowest
precedence: a tool name listed in `KLOC_DENY_TOOLS` short-circuits
before the evaluator runs.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.hooks_audit import stat_client
from src.hooks_audit.policy import Policy
from src.settings import (
    FileReadLimits,
    KlocFlowsLimits,
    Settings,
    ToolLimitsConfig,
)


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _stub_mode_env(monkeypatch):
    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    yield


def _settings(
    *,
    file_read: FileReadLimits | None = None,
    kloc_flows: KlocFlowsLimits | None = None,
    deny_tools: str = "",
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        kloc_deny_tools=deny_tools,
        tool_limits=ToolLimitsConfig(file_read=file_read, kloc_flows=kloc_flows),
    )


def _before(tool_name: str, args: dict[str, Any] | None = None) -> dict:
    return {
        "event": "BeforeToolCall",
        "payload": {"tool_name": tool_name, "args": args or {}},
    }


def _patch_stat(monkeypatch, response: dict | None) -> dict[str, int]:
    calls: dict[str, int] = {"count": 0}

    async def _fake_stat(path: str, *, base_url: str | None = None):
        calls["count"] += 1
        return response

    monkeypatch.setattr(stat_client, "stat", _fake_stat)
    return calls


async def test_file_read_under_cap_allows(monkeypatch) -> None:
    _patch_stat(
        monkeypatch,
        {"exists": True, "size_bytes": 200_000, "is_file": True},
    )
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=262_144)))
    decision = await policy.decide(
        _before("file_read", {"path": "/workspace/small.json"})
    )
    assert decision == {"decision": "allow"}


async def test_file_read_over_cap_denies_with_hint(monkeypatch) -> None:
    _patch_stat(
        monkeypatch,
        {"exists": True, "size_bytes": 5_242_880, "is_file": True},
    )
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=262_144)))
    decision = await policy.decide(
        _before("file_read", {"path": "/workspace/vendor/big.json"})
    )
    assert decision["decision"] == "deny"
    assert decision["reason"] == "tool_limit:file_too_large"
    hint = decision["hint"]
    assert "5.0 MiB" in hint
    assert "256 KiB" in hint
    assert "start_line" in hint


async def test_file_read_byte_range_bypasses_stat(monkeypatch) -> None:
    calls = _patch_stat(
        monkeypatch,
        {"exists": True, "size_bytes": 5_242_880, "is_file": True},
    )
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=262_144)))
    decision = await policy.decide(
        _before(
            "file_read",
            {"path": "/workspace/big.json", "start_line": 1, "end_line": 100},
        )
    )
    assert decision == {"decision": "allow"}
    assert calls["count"] == 0


async def test_file_read_missing_path_allows(monkeypatch) -> None:
    calls = _patch_stat(monkeypatch, None)
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=262_144)))
    decision = await policy.decide(_before("file_read", {}))
    assert decision == {"decision": "allow"}
    assert calls["count"] == 0


async def test_file_read_stat_timeout_allows(monkeypatch) -> None:
    _patch_stat(monkeypatch, None)
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=262_144)))
    decision = await policy.decide(
        _before("file_read", {"path": "/workspace/maybe.json"})
    )
    assert decision == {"decision": "allow"}


async def test_kloc_flows_unbounded_denies() -> None:
    policy = Policy(_settings(kloc_flows=KlocFlowsLimits(require_bounded=True)))
    decision = await policy.decide(_before("kloc_flows", {}))
    assert decision["decision"] == "deny"
    assert decision["reason"] == "tool_limit:unbounded"
    assert "depth" in decision["hint"]
    assert "limit" in decision["hint"]


async def test_kloc_flows_with_depth_allows() -> None:
    policy = Policy(_settings(kloc_flows=KlocFlowsLimits(require_bounded=True)))
    decision = await policy.decide(_before("kloc_flows", {"depth": 2}))
    assert decision == {"decision": "allow"}


async def test_kloc_flows_with_limit_allows() -> None:
    policy = Policy(_settings(kloc_flows=KlocFlowsLimits(require_bounded=True)))
    decision = await policy.decide(_before("kloc_flows", {"limit": 50}))
    assert decision == {"decision": "allow"}


async def test_unknown_tool_allows() -> None:
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=1)))
    decision = await policy.decide(_before("some_new_tool", {}))
    assert decision == {"decision": "allow"}


async def test_deny_set_precedence_wins_over_evaluator(monkeypatch) -> None:
    calls = _patch_stat(
        monkeypatch,
        {"exists": True, "size_bytes": 5_242_880, "is_file": True},
    )
    policy = Policy(
        _settings(
            file_read=FileReadLimits(max_bytes=1),
            deny_tools="file_read",
        )
    )
    decision = await policy.decide(
        _before("file_read", {"path": "/workspace/anything"})
    )
    assert decision == {"decision": "deny", "reason": "test-deny:file_read"}
    assert calls["count"] == 0


async def test_malformed_tool_limits_raises_at_boot(monkeypatch, tmp_path) -> None:
    from pydantic import ValidationError

    monkeypatch.setenv("KLOC_STUB_MODE", "true")
    monkeypatch.setenv("KLOC_TOOL_LIMITS", "{invalid")
    monkeypatch.chdir(tmp_path)
    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None)  # type: ignore[call-arg]


async def test_empty_tool_limits_allows_file_read(monkeypatch) -> None:
    calls = _patch_stat(
        monkeypatch,
        {"exists": True, "size_bytes": 5_242_880, "is_file": True},
    )
    policy = Policy(_settings())
    decision = await policy.decide(
        _before("file_read", {"path": "/workspace/anything"})
    )
    assert decision == {"decision": "allow"}
    assert calls["count"] == 0


async def test_non_before_tool_call_allows() -> None:
    policy = Policy(_settings(file_read=FileReadLimits(max_bytes=1)))
    decision = await policy.decide(
        {"event": "AfterToolCall", "payload": {"tool_name": "file_read"}}
    )
    assert decision == {"decision": "allow"}
