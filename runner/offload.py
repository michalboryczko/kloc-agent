"""Oversized-frame offload helper for the runner channel.

Constructs the `(frame) -> rewritten_frame | None` callback that
`BackendChannel` invokes for any AG-UI frame whose serialised length
exceeds `RUNNER_MAX_FRAME_BYTES`. The callback uploads the bulky body
to S3 and registers it through the existing `ArtifactRegistered`
webhook, then returns a `ToolCallResult` whose `content` references
the artifact id.

The actual S3 client is intentionally injectable (`uploader`) so the
unit tests do not require aioboto3 + MinIO.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


ARTIFACT_MIME = "application/x-agui-frame"
ARTIFACT_BUCKET_ENV = "ARTIFACT_BUCKET"


Uploader = Callable[[str, str, bytes, str], Awaitable[None]]


async def _noop_uploader(
    bucket: str, key: str, body: bytes, content_type: str
) -> None:
    log.warning(
        "offload.no_s3_uploader_configured; "
        "skipping upload bucket=%s key=%s bytes=%d",
        bucket,
        key,
        len(body),
    )


def build_oversize_offload(
    *,
    audit_sender: Any,
    session_id: str,
    run_id_provider: Callable[[], str],
    bucket: str | None = None,
    uploader: Uploader | None = None,
) -> Callable[[dict], Awaitable[dict | None]]:
    """Return an offload callback bound to the running audit sender.

    The returned callback is what `BackendChannel.set_offload_oversize`
    expects: takes the original AG-UI frame, side-effects the artifact
    upload + webhook, and returns the rewritten frame.
    """
    effective_bucket = bucket or os.environ.get(ARTIFACT_BUCKET_ENV) or ""
    effective_uploader: Uploader = uploader or _noop_uploader

    async def offload(frame: dict) -> dict | None:
        body_str = json.dumps(frame)
        body_bytes = body_str.encode("utf-8")
        size = len(body_bytes)
        sha256_hex = hashlib.sha256(body_bytes).hexdigest()
        artifact_id_hint = uuid.uuid4().hex
        filename = f"agui-frame-{artifact_id_hint}.json"
        object_key = (
            f"sessions/{session_id}/artifacts/{artifact_id_hint}/{filename}"
        )

        if not effective_bucket:
            log.error("offload.no_bucket_configured; dropping oversized frame")
            return None

        try:
            await effective_uploader(
                effective_bucket, object_key, body_bytes, ARTIFACT_MIME
            )
        except Exception:
            log.exception("offload.upload_failed; dropping oversized frame")
            return None

        try:
            response = await audit_sender.register_artifact(
                filename=filename,
                content_type=ARTIFACT_MIME,
                size_bytes=size,
                bucket=effective_bucket,
                object_key=object_key,
                sha256_hex=sha256_hex,
            )
        except Exception:
            log.exception("offload.register_failed; dropping oversized frame")
            return None

        if not response or not response.get("artifact_id"):
            log.error(
                "offload.register_returned_no_artifact_id; dropping frame"
            )
            return None
        artifact_id = str(response["artifact_id"])

        tool_call_id = (
            frame.get("toolCallId")
            or frame.get("tool_call_id")
            or _value_field(frame, ("toolCallId", "tool_call_id"))
        )
        try:
            run_id = run_id_provider()
        except Exception:
            run_id = None

        rewritten: dict[str, Any] = {
            "type": "TOOL_CALL_RESULT",
            "runId": run_id,
            "value": {
                "toolCallId": tool_call_id,
                "content": {
                    "artifactId": artifact_id,
                    "bucket": effective_bucket,
                    "objectKey": object_key,
                    "sizeBytes": size,
                    "sha256": sha256_hex,
                    "mimeType": ARTIFACT_MIME,
                    "offloaded": True,
                    "originalType": frame.get("type"),
                },
            },
        }
        return rewritten

    return offload


def _value_field(frame: dict, keys: tuple[str, ...]) -> Any:
    value = frame.get("value")
    if not isinstance(value, dict):
        return None
    for k in keys:
        if k in value:
            return value[k]
    return None
