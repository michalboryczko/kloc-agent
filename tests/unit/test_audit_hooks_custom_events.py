"""Tests for the two CUSTOM AG-UI events emitted by `AuditHookSender`:

- `ToolCallDenied` — fired whenever the runner is forced to cancel a
  tool call (webhook deny, deadline, fail-closed).
- `ArtifactAttached` — fired after a successful `ArtifactRegistered`
  webhook round-trip so the FE can render an artifact chip without
  polling.
"""

from __future__ import annotations

import asyncio
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
    emitted: list[dict], post_responses: list[dict | Exception]
) -> AuditHookSender:
    sender = AuditHookSender(
        backend_url="http://unused",
        runner_id="rid",
        session_id="sid",
        run_id_provider=lambda: "rA",
        runner_secret="secret",
        emit_custom_event=_make_emit(emitted),
    )

    async def _fake_post(self: AuditHookSender, payload: dict, event_name: str) -> dict:
        if not post_responses:
            return {}
        item = post_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    AuditHookSender._post = _fake_post  # type: ignore[assignment]
    return sender


def _make_emit(emitted: list[dict]):
    async def _emit(event: dict) -> None:
        emitted.append(event)

    return _emit


async def test_deny_decision_emits_tool_call_denied() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[{"decision": "deny", "reason": "deny_list"}],
    )
    event = _ToolEvent(tool_call_id="tc-42", tool_name="read_file")

    await sender.before_tool_call(event)

    assert event.cancel_tool == "deny_list"
    custom_events = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert len(custom_events) == 1
    frame = custom_events[0]
    assert frame == {
        "type": "CUSTOM",
        "name": "ToolCallDenied",
        "value": {
            "toolCallId": "tc-42",
            "toolName": "read_file",
            "reason": "deny_list",
        },
    }


async def test_deny_default_reason_when_webhook_omits_it() -> None:
    emitted: list[dict] = []
    sender = _make_sender(emitted, post_responses=[{"decision": "deny"}])
    event = _ToolEvent(tool_call_id="tc-9", tool_name="exec")

    await sender.before_tool_call(event)

    assert event.cancel_tool == "policy_denied"
    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert denied[0]["value"]["reason"] == "policy_denied"


async def test_deadline_emits_tool_call_denied_alongside_backpressure() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted, post_responses=[httpx.TimeoutException("simulated")]
    )
    event = _ToolEvent(tool_call_id="tc-7", tool_name="slow_tool")

    await sender.before_tool_call(event)

    assert event.cancel_tool == "policy_deadline_exceeded"
    names = [e.get("name") for e in emitted]
    assert "HookBackpressure" in names
    assert "ToolCallDenied" in names
    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"][0]
    assert denied["value"]["reason"] == "policy_deadline_exceeded"
    assert denied["value"]["toolCallId"] == "tc-7"


async def test_unexpected_post_failure_fails_closed_with_denied_event() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted, post_responses=[RuntimeError("backend exploded")]
    )
    event = _ToolEvent(tool_call_id="tc-13", tool_name="any")

    await sender.before_tool_call(event)

    assert event.cancel_tool == "policy_unavailable"
    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert denied[0]["value"]["reason"] == "policy_unavailable"


async def test_allow_decision_does_not_emit_tool_call_denied() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted, post_responses=[{"decision": "allow"}]
    )
    event = _ToolEvent()

    await sender.before_tool_call(event)

    assert event.cancel_tool is None
    assert not any(e.get("name") == "ToolCallDenied" for e in emitted)


async def test_denial_without_tool_call_id_emits_no_event() -> None:
    """No usable toolCallId means the FE can't correlate the denial to a
    pending ToolCallStart, so suppress the emit rather than send a frame
    the reducer will silently drop."""
    emitted: list[dict] = []
    sender = _make_sender(
        emitted, post_responses=[{"decision": "deny", "reason": "deny_list"}]
    )
    event = _ToolEvent()
    event.tool_use = {"name": "x", "input": {}}

    await sender.before_tool_call(event)

    assert not any(e.get("name") == "ToolCallDenied" for e in emitted)


async def test_double_denial_for_same_tool_call_id_emits_once() -> None:
    """Contract is exactly-once per toolCallId. A retry of before_tool_call
    on the same tool use must not produce a second CUSTOM frame."""
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[
            {"decision": "deny", "reason": "deny_list"},
            {"decision": "deny", "reason": "deny_list"},
        ],
    )
    event_a = _ToolEvent(tool_call_id="tc-77", tool_name="read_file")
    event_b = _ToolEvent(tool_call_id="tc-77", tool_name="read_file")

    await sender.before_tool_call(event_a)
    await sender.before_tool_call(event_b)

    denied = [e for e in emitted if e.get("name") == "ToolCallDenied"]
    assert len(denied) == 1
    assert denied[0]["value"]["toolCallId"] == "tc-77"


async def test_register_artifact_emits_artifact_attached_on_success() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[{"artifact_id": "a-1", "created": True}],
    )

    response = await sender.register_artifact(
        filename="jwt-flow.md",
        content_type="text/markdown",
        size_bytes=2147,
        bucket="kloc-agent-artifacts-dev",
        object_key="sessions/s/artifacts/a-1/jwt-flow.md",
        sha256_hex="0" * 64,
    )

    assert response == {"artifact_id": "a-1", "created": True}
    attached = [e for e in emitted if e.get("name") == "ArtifactAttached"]
    assert len(attached) == 1
    assert attached[0] == {
        "type": "CUSTOM",
        "name": "ArtifactAttached",
        "value": {
            "artifactId": "a-1",
            "filename": "jwt-flow.md",
            "sizeBytes": 2147,
            "mimeType": "text/markdown",
        },
    }


async def test_register_artifact_includes_parent_message_id_when_provided() -> None:
    """FE uses `parentMessageId` to route the chip to a specific
    assistant message bubble (see `frontend/src/lib/types.ts`)."""
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[{"artifact_id": "a-3", "created": True}],
    )

    await sender.register_artifact(
        filename="report.md",
        content_type="text/markdown",
        size_bytes=100,
        bucket="b",
        object_key="k",
        sha256_hex="0" * 64,
        message_id="msg-77",
    )

    attached = [e for e in emitted if e.get("name") == "ArtifactAttached"][0]
    assert attached["value"]["parentMessageId"] == "msg-77"


async def test_register_artifact_omits_parent_message_id_when_absent() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[{"artifact_id": "a-4", "created": True}],
    )

    await sender.register_artifact(
        filename="report.md",
        content_type="text/markdown",
        size_bytes=100,
        bucket="b",
        object_key="k",
        sha256_hex="0" * 64,
    )

    attached = [e for e in emitted if e.get("name") == "ArtifactAttached"][0]
    assert "parentMessageId" not in attached["value"]


async def test_register_artifact_omits_mime_type_when_unknown() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[{"artifact_id": "a-2", "created": True}],
    )

    await sender.register_artifact(
        filename="binary.dat",
        content_type="",
        size_bytes=10,
        bucket="b",
        object_key="k",
        sha256_hex="f" * 64,
    )

    attached = [e for e in emitted if e.get("name") == "ArtifactAttached"]
    assert "mimeType" not in attached[0]["value"]


async def test_register_artifact_does_not_emit_when_webhook_fails() -> None:
    emitted: list[dict] = []
    sender = _make_sender(
        emitted, post_responses=[RuntimeError("backend exploded")]
    )

    response = await sender.register_artifact(
        filename="x.md",
        content_type="text/markdown",
        size_bytes=1,
        bucket="b",
        object_key="k",
        sha256_hex="a" * 64,
    )

    assert response is None
    assert not any(e.get("name") == "ArtifactAttached" for e in emitted)


async def test_register_artifact_does_not_emit_when_response_lacks_id() -> None:
    emitted: list[dict] = []
    sender = _make_sender(emitted, post_responses=[{"created": False}])

    response = await sender.register_artifact(
        filename="x.md",
        content_type="text/markdown",
        size_bytes=1,
        bucket="b",
        object_key="k",
        sha256_hex="a" * 64,
    )

    assert response == {"created": False}
    assert not any(e.get("name") == "ArtifactAttached" for e in emitted)


async def test_double_register_for_same_artifact_id_emits_once() -> None:
    """A re-register of the same artifact (idempotent webhook returns
    `created=false` with the same artifact_id) must not produce a
    second CUSTOM frame on the same run."""
    emitted: list[dict] = []
    sender = _make_sender(
        emitted,
        post_responses=[
            {"artifact_id": "a-9", "created": True},
            {"artifact_id": "a-9", "created": False},
        ],
    )

    await sender.register_artifact(
        filename="report.md",
        content_type="text/markdown",
        size_bytes=42,
        bucket="b",
        object_key="k",
        sha256_hex="0" * 64,
    )
    await sender.register_artifact(
        filename="report.md",
        content_type="text/markdown",
        size_bytes=42,
        bucket="b",
        object_key="k",
        sha256_hex="0" * 64,
    )

    attached = [e for e in emitted if e.get("name") == "ArtifactAttached"]
    assert len(attached) == 1
    assert attached[0]["value"]["artifactId"] == "a-9"
