"""aioboto3 helpers.

The S3 client is lifespan-managed on `app.state.s3`. These helpers
take the client as a parameter so handlers can call them without
importing FastAPI state directly.
"""
from __future__ import annotations

from typing import Any


async def upload_bytes(
    s3: Any,
    bucket: str,
    key: str,
    body: bytes,
    content_type: str,
) -> None:
    await s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )


async def presigned_get(
    s3: Any,
    bucket: str,
    key: str,
    expires_in: int = 900,
) -> str:
    return await s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def artifact_object_key(session_id: str, artifact_id: str, filename: str) -> str:
    """Canonical S3 object key scheme."""
    return f"sessions/{session_id}/artifacts/{artifact_id}/{filename}"
