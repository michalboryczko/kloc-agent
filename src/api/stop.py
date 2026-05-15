"""Run cancel endpoint (Phase 1.C-1.7).

POST /v1/sessions/{id}/runs/{run_id}/cancel -> 204

Marks the run cancelled in the registry; dev-2's runner_registry
implements `cancel(handle, run_id)` to forward the signal.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.deps import get_session
from src.repos.sessions import SessionRepo


router = APIRouter(tags=["stop"])


@router.post(
    "/sessions/{session_id}/runs/{run_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_run(
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> None:
    sessions = SessionRepo(db)
    if (await sessions.get(session_id)) is None:
        raise HTTPException(status_code=404, detail="session not found")

    registry = getattr(request.app.state, "runner_registry", None)
    cancel = getattr(registry, "cancel", None) if registry else None
    if cancel is not None:
        try:
            await cancel(str(session_id), str(run_id))
        except Exception:
            # Best-effort cancel; the registry may already be torn down.
            pass

    return None
