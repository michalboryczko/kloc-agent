"""Health endpoints.

`/healthz`: 200 once the process is up (compose healthcheck / liveness).
`/readyz`: 200 only when DB and S3 are reachable; 503 with a JSON reason
otherwise. Used as a readiness gate before serving traffic.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text


router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    overall_ok = True

    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        checks["db"] = "engine-not-initialized"
        overall_ok = False
    else:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as e:  # pragma: no cover - defensive
            checks["db"] = f"error:{type(e).__name__}"
            overall_ok = False

    s3 = getattr(request.app.state, "s3", None)
    if s3 is None:
        checks["s3"] = "client-not-initialized"
        overall_ok = False
    else:
        try:
            await s3.list_buckets()
            checks["s3"] = "ok"
        except Exception as e:  # pragma: no cover - defensive
            checks["s3"] = f"error:{type(e).__name__}"
            overall_ok = False

    return JSONResponse(
        {"status": "ok" if overall_ok else "degraded", "checks": checks},
        status_code=status.HTTP_200_OK
        if overall_ok
        else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
