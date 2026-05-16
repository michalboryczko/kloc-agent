"""Artifact endpoints.

GET /v1/artifacts/{id} -> 302 redirect to a presigned MinIO URL.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.deps import get_session
from src.repos.artifacts import ArtifactRepo
from src.storage.s3 import presigned_get


router = APIRouter(tags=["artifacts"])


@router.get("/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    s3 = getattr(request.app.state, "s3", None)
    if s3 is None:
        raise HTTPException(
            status_code=503, detail="S3 client not initialized"
        )

    repo = ArtifactRepo(db)
    row = await repo.get(artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    url = await presigned_get(
        s3=s3, bucket=row.bucket, key=row.object_key, expires_in=900
    )
    return RedirectResponse(url=url, status_code=302)
