"""HMAC webhook receiver.

POST /v1/webhooks/runners/{runner_id}/events

Invariants:
- Verify HMAC over `f"{ts}.{raw_body}"`.
- Replay window 60 s.
- Persist the `audit_log` row in the same DB tx as the policy decision.
- Respond `202 {decision, reason?}`. Policy defaults to allow; the deny
  path is driven by `KLOC_DENY_TOOLS`.

The runner enforces the 2 s deadline and emits a `HookBackpressure`
CustomEvent on the AG-UI stream when the backend is slow.

Supported event names (`X-Kloc-Hook-Event` / `body.event`):
- `BeforeToolCall`, `AfterToolCall` — Strands lifecycle hooks.
- `ArtifactRegistered` — runner finished an S3 upload; backend inserts
  `artifact_metadata`. Idempotent via `ArtifactRepo.register`.

Heartbeats do NOT flow here — they arrive over the JSONL ingress and
reset the per-session heartbeat watcher.
"""
from __future__ import annotations

import functools
import json
import logging
import sys
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.deps import get_session
from src.hooks_audit.policy import Policy
from src.hooks_audit.verify_hmac import verify_hmac_signature
from src.repos.artifacts import ArtifactRepo
from src.repos.audit import AuditRepo
from src.settings import get_settings


router = APIRouter(tags=["webhooks"])
log = logging.getLogger("kloc_agent.webhooks")


@functools.lru_cache(maxsize=1)
def _diag_enabled() -> bool:
    # Settings construction may raise (e.g. missing provider key); diagnostics
    # are best-effort, so a build failure means "off", never a crash.
    try:
        return bool(get_settings().diag_events)
    except Exception:
        return False


def _diag(msg: str) -> None:
    """Off by default. Direct stderr bypasses uvicorn's --log-config
    which can filter `kloc_agent.*` INFO records in some images."""
    if not _diag_enabled():
        return
    print(msg, file=sys.stderr, flush=True)


def _audit_event_name(event: str, decision: str | None) -> str:
    """Map a hook event + decision to one of the 12 locked audit names."""
    if event == "BeforeToolCall":
        if decision == "deny":
            return "tool_call.denied"
        return "tool_call.started"
    if event == "AfterToolCall":
        return "tool_call.completed"
    if event == "ArtifactRegistered":
        return "artifact_registered"
    # Heartbeat and other lifecycle events have no canonical audit row in
    # the locked vocabulary; raise so we don't silently drop on the floor.
    raise HTTPException(
        status_code=400,
        detail=f"unsupported webhook event: {event!r}",
    )


@router.post(
    "/webhooks/runners/{runner_id}/events",
    status_code=status.HTTP_202_ACCEPTED,
)
async def receive_runner_event(
    runner_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    x_kloc_hook_event: str | None = Header(default=None),
    x_kloc_hook_ts: str | None = Header(default=None),
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    settings = get_settings()

    if not authorization or not x_kloc_hook_ts:
        raise HTTPException(status_code=401, detail="missing auth headers")

    try:
        ts_ms = int(x_kloc_hook_ts)
    except ValueError:
        raise HTTPException(status_code=400, detail="bad X-Kloc-Hook-Ts")

    raw_body = await request.body()

    _diag(
        f"auth rx: runner_id={runner_id} ts_hdr={x_kloc_hook_ts} "
        f"body_len={len(raw_body)} "
        f"sig_hdr_prefix={(authorization or '')[:24]!r}"
    )

    # Per-runner secret. The runner is given a unique `runner_secret` in
    # its HydrationPayload by `RunnerRegistry.get_or_spawn`; the receiver
    # must look that up by `runner_id`. The bootstrap secret in settings
    # is only a last-resort fallback for dev / unit tests, gated by
    # `settings.allow_hmac_fallback` (default False = strict).
    secret, secret_source = await _resolve_runner_secret(
        request,
        runner_id,
        settings.kloc_hook_secret,
        settings.allow_hmac_fallback,
    )
    _diag(
        f"auth secret lookup: runner_id={runner_id} "
        f"source={secret_source} secret_len={len(secret) if secret else 0}"
    )

    if secret_source == "no_entry":
        # Strict mode: registry is wired, has no entry for this runner_id,
        # and the operator has not opted into the bootstrap fallback.
        # Reject BEFORE running HMAC verify so the global secret is never
        # tested against a request claiming an unknown runner_id.
        _diag(
            f"auth fail: reason=strict_no_runner_entry "
            f"runner_id={runner_id} secret_source={secret_source}"
        )
        raise HTTPException(status_code=401, detail="unknown runner")

    if not verify_hmac_signature(raw_body, ts_ms, authorization, secret):
        _diag(
            f"auth fail: reason=hmac_or_replay runner_id={runner_id} "
            f"secret_source={secret_source} ts_ms={ts_ms} "
            f"body_len={len(raw_body)}"
        )
        raise HTTPException(
            status_code=401,
            detail="invalid hmac or stale timestamp",
        )
    _diag(f"auth pass: runner_id={runner_id}")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="body not valid JSON")

    event_name = body.get("event") or x_kloc_hook_event or ""
    if not event_name:
        raise HTTPException(status_code=400, detail="missing event name")

    # Policy decision — PoC allow-all unless KLOC_DENY_TOOLS matches.
    policy = Policy(settings)
    decision = await policy.decide(body)

    # Persist audit row + side effects (artifact register) + policy
    # decision in the SAME transaction.
    session_id = _parse_uuid(body.get("session_id"))
    payload = body.get("payload") or {}
    # Artifact webhooks key off `message_id`; tool-call webhooks carry
    # `parent_message_id` set by the runner's AG-UI emitter wrapper so
    # the persister can resolve `tool_calls.parent_message_id` without
    # replaying the event stream.
    message_id = _parse_uuid(payload.get("message_id")) or _parse_uuid(
        payload.get("parent_message_id")
    )
    audit_event = _audit_event_name(event_name, decision.get("decision"))

    # ArtifactRegistered: insert artifact_metadata idempotently. The
    # ON CONFLICT (session_id, object_key) absorbs duplicate webhooks
    # from runner retries. The `artifact_registered` audit row is
    # emitted unconditionally — even on duplicate insert — so the audit
    # trail records every receive.
    response_extra: dict = {}
    if event_name == "ArtifactRegistered":
        if session_id is None:
            raise HTTPException(
                status_code=400,
                detail="ArtifactRegistered requires session_id",
            )
        try:
            sha256_hex = str(payload["sha256"])
            sha256_bytes = bytes.fromhex(sha256_hex)
        except (KeyError, ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="ArtifactRegistered.payload.sha256 must be hex string",
            )
        try:
            row, created = await ArtifactRepo(db).register(
                session_id=session_id,
                message_id=message_id,
                filename=str(payload["filename"]),
                content_type=str(payload["content_type"]),
                size_bytes=int(payload["size_bytes"]),
                bucket=str(payload["bucket"]),
                object_key=str(payload["object_key"]),
                sha256=sha256_bytes,
            )
        except KeyError as e:
            raise HTTPException(
                status_code=400,
                detail=f"ArtifactRegistered.payload missing field: {e.args[0]}",
            )
        response_extra = {"artifact_id": str(row.id), "created": created}

    # In-flight tool-call tracking on the registry. The
    # `tool_call.crashed` audit emission needs every BeforeToolCall
    # ALLOW registered and every AfterToolCall (or BeforeToolCall DENY)
    # unregistered. Denied calls never fire and so can't crash; the
    # bookkeeping happens in the same handler so a missed call here
    # would mean a missed `crashed` emit if the runner dies.
    registry = getattr(request.app.state, "runner_registry", None)
    if registry is not None and session_id is not None:
        sid_str = str(session_id)
        tool_call_id = payload.get("tool_call_id")
        tool_name = payload.get("tool_name") or ""
        decision_str = decision.get("decision")
        if (
            event_name == "BeforeToolCall"
            and decision_str == "allow"
            and tool_call_id
        ):
            on_started = getattr(registry, "on_tool_call_started", None)
            if on_started is not None:
                try:
                    await on_started(sid_str, str(tool_call_id), str(tool_name))
                except Exception:  # pragma: no cover - defensive
                    pass
        elif tool_call_id and (
            event_name == "AfterToolCall"
            or (event_name == "BeforeToolCall" and decision_str == "deny")
        ):
            on_completed = getattr(registry, "on_tool_call_completed", None)
            if on_completed is not None:
                try:
                    await on_completed(sid_str, str(tool_call_id))
                except Exception:  # pragma: no cover - defensive
                    pass

    audit = AuditRepo(db)
    await audit.append(
        event_type=audit_event,
        actor=f"runner:{runner_id}",
        session_id=session_id,
        message_id=message_id,
        payload={
            "event": event_name,
            "runner_id": runner_id,
            "run_id": body.get("run_id"),
            "received_ts_ms": int(time.time() * 1000),
            "hook_ts_ms": ts_ms,
            "payload": payload,
            "decision": decision,
        },
    )
    await db.commit()

    return JSONResponse(
        content={**decision, **response_extra},
        status_code=status.HTTP_202_ACCEPTED,
    )


def _parse_uuid(v: str | None) -> uuid.UUID | None:
    if not v:
        return None
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


async def _resolve_runner_secret(
    request: Request,
    runner_id: str,
    fallback_secret: str,
    allow_fallback: bool,
) -> tuple[str, str]:
    """Look up the per-runner HMAC secret.

    Returns `(secret, source)` where `source` is one of:
      - "registry" — looked up via `RunnerRegistry.get_by_runner_id` and
        read off `entry.handle.runner_secret` (the canonical per-runner
        secret minted at spawn).
      - "fallback" — bootstrap secret from `settings.kloc_hook_secret`,
        used when the registry exists but has no entry for this runner_id
        AND `allow_fallback` is True (legacy permissive mode for dev).
      - "no_registry" — registry is not wired on `request.app.state`
        (unit tests / stub mode); falls back to the bootstrap secret
        unconditionally because there is no source of per-runner secrets
        to compare against.
      - "no_entry" — registry IS wired but has no entry for this
        runner_id and `allow_fallback` is False (strict, default). The
        caller MUST reject the request with 401 before attempting HMAC
        verification — accepting the bootstrap secret here would let a
        request claiming any runner_id authenticate against the global
        secret.

    The source string is logged so operator smoke checks can
    distinguish a legitimate per-runner match from the fallback path.
    """
    registry = getattr(request.app.state, "runner_registry", None)
    if registry is None:
        # No registry wired at all (unit-test / stub path). Preserve the
        # historical behaviour of accepting the bootstrap secret — the
        # strict flag only governs the registry-present-but-empty case.
        return fallback_secret, "no_registry"

    get_by_id = getattr(registry, "get_by_runner_id", None)
    if get_by_id is None:
        return fallback_secret, "no_registry"

    try:
        entry = await get_by_id(runner_id)
    except Exception:
        log.exception(
            "registry.get_by_runner_id raised; "
            "falling back to bootstrap secret",
        )
        entry = None

    if entry is not None:
        handle = getattr(entry, "handle", None)
        secret = getattr(handle, "runner_secret", None)
        if secret:
            return secret, "registry"

    # Registry is wired but has no entry for this runner_id.
    if allow_fallback:
        return fallback_secret, "fallback"
    return "", "no_entry"
