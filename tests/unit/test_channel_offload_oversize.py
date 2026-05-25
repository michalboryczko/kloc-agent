"""Oversized AG-UI frames are rerouted to the artifact-upload path
before they are enqueued onto the outbound JSONL stream. The rewritten
frame must reference the returned `artifact_id` and serialise under
`RUNNER_MAX_FRAME_BYTES`.
"""
from __future__ import annotations

import json

import pytest

from runner.channel import RUNNER_MAX_FRAME_BYTES, BackendChannel

pytestmark = pytest.mark.unit


def _serialised_len(frame: dict) -> int:
    return len(json.dumps(frame).encode("utf-8"))


def _make_oversize_tool_result() -> dict:
    # 16 MiB of payload — comfortably over the 15 MiB runner cap.
    big_string = "x" * (16 * 1024 * 1024)
    return {
        "type": "TOOL_CALL_RESULT",
        "runId": "run-1",
        "value": {
            "toolCallId": "tc-1",
            "content": big_string,
        },
    }


async def test_offload_oversize_replaces_frame_with_artifact_reference() -> None:
    calls: list[dict] = []

    async def offload(frame: dict) -> dict:
        calls.append(frame)
        return {
            "type": "TOOL_CALL_RESULT",
            "runId": frame.get("runId"),
            "value": {
                "toolCallId": frame["value"]["toolCallId"],
                "content": {
                    "artifactId": "artifact-uuid",
                    "offloaded": True,
                    "sizeBytes": _serialised_len(frame),
                },
            },
        }

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run-1",
        offload_oversize=offload,
    )

    oversize = _make_oversize_tool_result()
    assert _serialised_len(oversize) > RUNNER_MAX_FRAME_BYTES

    await chan.emit(oversize)

    assert len(calls) == 1, "offload callback must be invoked exactly once"
    queued = chan._outbound.get_nowait()
    assert queued is not None
    assert queued["value"]["content"]["artifactId"] == "artifact-uuid"
    assert _serialised_len(queued) < RUNNER_MAX_FRAME_BYTES


async def test_undersize_frame_does_not_trigger_offload() -> None:
    calls: list[dict] = []

    async def offload(frame: dict) -> dict:
        calls.append(frame)
        return frame

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run-1",
        offload_oversize=offload,
    )

    small = {"type": "TEXT_MESSAGE_CONTENT", "delta": "hello"}
    await chan.emit(small)
    assert calls == [], "offload must NOT be invoked for under-cap frames"
    queued = chan._outbound.get_nowait()
    assert queued == small


async def test_oversize_with_no_offload_drops_frame_without_enqueueing() -> None:
    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run-1",
    )

    oversize = _make_oversize_tool_result()
    await chan.emit(oversize)
    assert chan._outbound.qsize() == 0


async def test_offload_returning_none_drops_frame() -> None:
    async def offload(frame: dict) -> None:
        return None

    chan = BackendChannel(
        backend_url="http://unused",
        session_id="s1",
        runner_id="r1",
        run_id_provider=lambda: "run-1",
        offload_oversize=offload,
    )
    oversize = _make_oversize_tool_result()
    await chan.emit(oversize)
    assert chan._outbound.qsize() == 0
