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
    ReadProjectFileLimits,
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
    read_project_file: ReadProjectFileLimits | None = None,
    deny_tools: str = "",
) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        kloc_deny_tools=deny_tools,
        tool_limits=ToolLimitsConfig(
            file_read=file_read,
            kloc_flows=kloc_flows,
            read_project_file=read_project_file,
        ),
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


# ---------------------------------------------------------------------------
# read_project_file evaluator
# ---------------------------------------------------------------------------


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects-source"
    proj = root / "kyc"
    src = proj / "src"
    src.mkdir(parents=True)
    monkeypatch.setenv("KLOC_PROJECTS_BACKEND_DIR", str(root))
    return root


async def test_read_project_file_oversize_denies_with_hint(
    projects_root,
) -> None:
    big = projects_root / "kyc" / "vendor"
    big.mkdir()
    big_file = big / "big.json"
    big_file.write_bytes(b"x" * (5 * 1024 * 1024))

    policy = Policy(
        _settings(read_project_file=ReadProjectFileLimits(max_bytes=262_144))
    )
    decision = await policy.decide(
        _before(
            "read_project_file",
            {"project_name": "kyc", "path": "vendor/big.json"},
        )
    )
    assert decision["decision"] == "deny"
    assert decision["reason"] == "tool_limit:file_too_large"
    assert "cap" in decision["hint"]
    assert "256 KiB" in decision["hint"]
    assert "start_line" in decision["hint"]


async def test_read_project_file_slice_bypasses_cap(projects_root) -> None:
    big = projects_root / "kyc" / "vendor"
    big.mkdir()
    big_file = big / "big.json"
    big_file.write_bytes(b"x" * (5 * 1024 * 1024))

    policy = Policy(
        _settings(read_project_file=ReadProjectFileLimits(max_bytes=262_144))
    )
    decision = await policy.decide(
        _before(
            "read_project_file",
            {
                "project_name": "kyc",
                "path": "vendor/big.json",
                "start_line": 1,
                "end_line": 50,
            },
        )
    )
    assert decision == {"decision": "allow"}


async def test_read_project_file_under_cap_allows(projects_root) -> None:
    small = projects_root / "kyc" / "src" / "A.php"
    small.write_text("<?php\necho 'hi';\n", encoding="utf-8")

    policy = Policy(
        _settings(read_project_file=ReadProjectFileLimits(max_bytes=262_144))
    )
    decision = await policy.decide(
        _before(
            "read_project_file",
            {"project_name": "kyc", "path": "src/A.php"},
        )
    )
    assert decision == {"decision": "allow"}


async def test_read_project_file_invalid_project_name_allows(
    projects_root,
) -> None:
    """An invalid project_name short-circuits to allow on the evaluator
    side; the runner-side tool surfaces the validation error string
    independently."""
    policy = Policy(
        _settings(read_project_file=ReadProjectFileLimits(max_bytes=1))
    )
    decision = await policy.decide(
        _before(
            "read_project_file",
            {"project_name": "..bad..", "path": "anything"},
        )
    )
    assert decision == {"decision": "allow"}


async def test_read_project_file_missing_project_allows(projects_root) -> None:
    policy = Policy(
        _settings(read_project_file=ReadProjectFileLimits(max_bytes=1))
    )
    decision = await policy.decide(
        _before(
            "read_project_file",
            {"project_name": "unknown", "path": "src/x"},
        )
    )
    assert decision == {"decision": "allow"}


async def test_read_project_file_no_limit_configured_allows(
    projects_root,
) -> None:
    """When tool_limits.read_project_file is not set, the evaluator
    has no opinion and the call flows through unconditionally."""
    policy = Policy(_settings())
    decision = await policy.decide(
        _before(
            "read_project_file",
            {"project_name": "kyc", "path": "src/A.php"},
        )
    )
    assert decision == {"decision": "allow"}
