"""Regression test: the AG-UI event formatter preserves
`parentMessageId` on `TOOL_CALL_START` events and on
`CUSTOM { name: ToolCallDenied }` events when present.

This field is the load-bearing piece of T05's parent-linking: the runner
stamps it at emission time, the backend forwards it verbatim through the
JSONL ingress and onto the SSE wire, and the frontend reducer groups
tool calls under the named assistant Message using it. A regression that
silently strips the field here would degrade gracefully on the FE (the
strict-drop fallback fires) but the symptom is "tool calls disappear",
which is worse than "tool calls land in the wrong bubble".
"""
from __future__ import annotations

import pytest

from src.streaming.agui_event_formatter import normalize

pytestmark = pytest.mark.unit


def test_normalize_preserves_parent_message_id_on_tool_call_start() -> None:
    out = normalize(
        {
            "type": "TOOL_CALL_START",
            "toolCallId": "tc-1",
            "toolCallName": "search_code",
            "parentMessageId": "msg-xyz",
        }
    )
    assert out["type"] == "TOOL_CALL_START"
    assert out["parentMessageId"] == "msg-xyz"
    assert out["toolCallId"] == "tc-1"
    assert out["toolCallName"] == "search_code"


def test_normalize_preserves_parent_message_id_on_tool_call_denied_custom() -> None:
    out = normalize(
        {
            "type": "CUSTOM",
            "name": "ToolCallDenied",
            "value": {
                "toolCallId": "tc-2",
                "toolName": "read_file",
                "reason": "deny_list",
                "parentMessageId": "msg-xyz",
            },
        }
    )
    assert out["type"] == "CUSTOM"
    assert out["name"] == "ToolCallDenied"
    assert out["value"]["parentMessageId"] == "msg-xyz"


def test_normalize_does_not_inject_parent_message_id_when_absent() -> None:
    out = normalize(
        {
            "type": "TOOL_CALL_START",
            "toolCallId": "tc-3",
            "toolCallName": "search_code",
        }
    )
    assert "parentMessageId" not in out


def test_normalize_pydantic_model_round_trips_parent_message_id() -> None:
    """The wire-typed path: when the runner yields a typed Pydantic
    `ToolCallStartEvent`, the normalizer must `model_dump(by_alias=True)`
    so the snake_case `parent_message_id` attribute round-trips as
    camelCase `parentMessageId` on the wire."""
    try:
        from ag_ui.core.events import ToolCallStartEvent  # type: ignore
    except ImportError:
        pytest.skip("ag_ui.core not installed in this environment")

    event = ToolCallStartEvent(
        type="TOOL_CALL_START",
        tool_call_id="tc-4",
        tool_call_name="search_code",
        parent_message_id="msg-typed",
    )
    out = normalize(event)
    assert out["parentMessageId"] == "msg-typed"
    assert out["toolCallId"] == "tc-4"
