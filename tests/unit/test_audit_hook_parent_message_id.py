"""Tests that `AuditHookSender` threads the active assistant message id
into the BeforeToolCall webhook payload and the ToolCallDenied custom
event.

The AG-UI emitter wrapper sets `_active_message_id` at TEXT_MESSAGE_START
and clears it at TEXT_MESSAGE_END. Tool-call audit emissions that fire
in between MUST carry `parent_message_id` (webhook payload) and
`parentMessageId` (CUSTOM event value); emissions outside that window
MUST omit those keys so the FE reducer can detect a runner regression.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from runner.hooks.audit import AuditHookSender

pytestmark = pytest.mark.unit


class _ToolEvent:
    """Minimal stand-in for the Strands BeforeToolCall event."""

    def __init__(
        self,
        tool_call_id: str = "tc-1",
        tool_name: str = "search_code",
        tool_input: dict | None = None,
    ) -> None:
        self.tool_use: dict[str, Any] = {
            "toolUseId": tool_call_id,
            "name": tool_name,
            "input": tool_input or {"q": "JWT_SECRET"},
        }
        self.cancel_tool: str | None = None


def _make_sender(
    emitted: list[dict],
    post_responses: list[dict | Exception],
    captured_payloads: list[dict] | None = None,
) -> AuditHookSender:
    async def _emit(event: dict) -> None:
        emitted.append(event)

    sender = AuditHookSender(
        backend_url="http://unused",
        runner_id="rid",
        session_id="sid",
        run_id_provider=lambda: "rA",
        runner_secret="secret",
        emit_custom_event=_emit,
    )

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        if captured_payloads is not None:
            captured_payloads.append(payload)
        if not post_responses:
            return {}
        item = post_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    AuditHookSender._post = _fake_post  # type: ignore[assignment]
    return sender


async def test_set_active_message_id_threads_into_before_payload() -> None:
    """`set_active_message_id("msg-abc")` followed by `before_tool_call`
    produces a webhook payload whose `payload.parent_message_id` is
    `"msg-abc"` — that's what the persister consumes to link
    `tool_calls.parent_message_id` to the assistant Message."""
    emitted: list[dict] = []
    posts: list[dict] = []
    sender = _make_sender(emitted, [{"decision": "allow"}], captured_payloads=posts)
    sender.set_active_message_id("msg-abc")

    await sender.before_tool_call(_ToolEvent(tool_call_id="tc-1"))

    before = [p for p in posts if p["event"] == "BeforeToolCall"]
    assert len(before) == 1
    assert before[0]["payload"]["parent_message_id"] == "msg-abc"
    assert before[0]["payload"]["tool_call_id"] == "tc-1"


async def test_before_payload_omits_parent_when_setter_not_called() -> None:
    """Before any TEXT_MESSAGE_START arrives, `_active_message_id` is
    `None`; the BeforeToolCall payload omits the field so the persister
    can distinguish 'not stamped' from 'stamped null'."""
    emitted: list[dict] = []
    posts: list[dict] = []
    sender = _make_sender(emitted, [{"decision": "allow"}], captured_payloads=posts)

    await sender.before_tool_call(_ToolEvent(tool_call_id="tc-2"))

    before = [p for p in posts if p["event"] == "BeforeToolCall"]
    assert "parent_message_id" not in before[0]["payload"]


async def test_clearing_active_message_id_omits_parent_from_denied() -> None:
    """`set_active_message_id(None)` MUST drop `parentMessageId` from
    subsequent `ToolCallDenied` custom-event payloads. This is the
    setter's clear-on-TEXT_MESSAGE_END contract."""
    emitted: list[dict] = []
    sender = _make_sender(emitted, [{"decision": "deny", "reason": "deny_list"}])
    sender.set_active_message_id("msg-xyz")
    sender.set_active_message_id(None)

    await sender.before_tool_call(_ToolEvent(tool_call_id="tc-3"))

    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert len(denied) == 1
    assert "parentMessageId" not in denied[0]["value"]


async def test_active_message_id_threads_into_denied_custom_event() -> None:
    """The `ToolCallDenied` CUSTOM event value carries the active
    message id in camelCase (`parentMessageId`), matching the AG-UI
    wire convention used by `ArtifactAttached`."""
    emitted: list[dict] = []
    sender = _make_sender(emitted, [{"decision": "deny", "reason": "deny_list"}])
    sender.set_active_message_id("msg-99")

    await sender.before_tool_call(_ToolEvent(tool_call_id="tc-4"))

    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert len(denied) == 1
    assert denied[0]["value"]["parentMessageId"] == "msg-99"
    assert denied[0]["value"]["toolCallId"] == "tc-4"


async def test_deadline_denial_carries_parent_message_id_when_active() -> None:
    """The deadline path also synthesises a `ToolCallDenied`; it MUST
    stamp `parentMessageId` consistently with the deny-decision path
    so the FE groups the deadline card under the right bubble."""
    emitted: list[dict] = []
    sender = _make_sender(emitted, [httpx.TimeoutException("simulated")])
    sender.set_active_message_id("msg-deadline")

    await sender.before_tool_call(_ToolEvent(tool_call_id="tc-5"))

    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert len(denied) == 1
    assert denied[0]["value"]["parentMessageId"] == "msg-deadline"


def test_setter_method_exists_and_is_callable_without_await() -> None:
    """Sanity check: `set_active_message_id` is a sync method (the AG-UI
    emitter wrapper invokes it inline between yields)."""
    sender = AuditHookSender(
        backend_url="http://unused",
        runner_id="rid",
        session_id="sid",
        run_id_provider=lambda: "rA",
        runner_secret="secret",
        emit_custom_event=lambda _e: None,  # type: ignore[arg-type]
    )
    assert hasattr(sender, "set_active_message_id")
    sender.set_active_message_id("msg-1")
    assert sender._active_message_id == "msg-1"
    sender.set_active_message_id(None)
    assert sender._active_message_id is None
