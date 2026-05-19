"""Streaming SSE endpoints.

Two routes:
  - `POST /v1/sessions/{id}/stream` — body is `RunAgentInput`; backend
    persists the user message, spawns/reuses the runner, sends the
    inbound message, and streams AG-UI events back as SSE.
  - `GET /v1/sessions/{id}/stream?run_id=...&last_event_id=...` —
    cursor replay + tail an in-flight run for reconnects.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Request

from src.db.engine import get_sessionmaker
from src.db.models import HydrationPayload, McpHttpEndpoint
from src.messaging.pgmq import (
    ensure_inbox_queue,
    inbox_queue_name,
    send_user_message,
)
from src.repos.audit import AuditRepo
from src.repos.messages import MessageRepo
from src.settings import get_settings
from src.streaming.agui_event_formatter import (
    is_run_lifecycle_terminal,
    normalize,
)
from src.streaming.debounce import TextDeltaDebouncer
from src.streaming.event_bus import event_bus
from src.streaming.execution_registry import execution_registry
from src.streaming.sse import make_response

log = logging.getLogger(__name__)

router = APIRouter()


def _get_runner_registry(request: Request):
    registry = getattr(request.app.state, "runner_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503,
            detail="runner_registry not initialised; backend lifespan failed",
        )
    return registry


@router.post("/sessions/{session_id}/stream")
async def stream_post(
    request: Request,
    session_id: str = Path(...),
):
    """Persist the user message BEFORE forwarding to the runner, then
    stream the AG-UI events back."""
    body = await request.json()
    run_id = body.get("runId") or body.get("run_id") or str(uuid.uuid4())
    messages = body.get("messages") or []

    session_uuid = _parse_uuid(session_id)
    if session_uuid is None:
        raise HTTPException(
            status_code=400,
            detail=f"invalid session_id {session_id!r} — must be a UUID",
        )

    registry = _get_runner_registry(request)

    # Build hydration BEFORE persisting the new user message so
    # `prior_messages` snapshots the DB state *without* the new turn.
    # The new turn arrives at the runner via PGMQ below, and the runner
    # merges `prior_messages + inbound.messages`; including the new
    # message in both lists doubles it in `RunAgentInput.messages` and
    # the UI renders the user bubble twice.
    hydration_payload = await _build_hydration_payload(
        session_id=session_id,
        session_uuid=session_uuid,
        run_id=run_id,
    )
    await _persist_user_message(session_uuid, messages)
    entry = await registry.get_or_spawn(session_id, hydration_payload)
    entry.warm_idle.on_user_message()

    # Subscribe-before-publish: register the SSE queue BEFORE the runner
    # is woken. A warm runner can begin emitting events the moment
    # `pgmq.send + NOTIFY` commits, and `event_bus.publish` drops events
    # when no subscriber queue exists yet. Registering first guarantees
    # the queue is in `_subs` when the runner's first event lands.
    queue = await event_bus.register(session_id, run_id)

    # Anything that raises between register and the StreamingResponse
    # actually iterating the generator leaks `queue` into `_subs`
    # forever (consume's finally never runs). Cleanup via unregister
    # on every error path before re-raising.
    try:
        # Enqueue the user_message on the per-session PGMQ queue and
        # NOTIFY the runner inside one transaction so the runner's
        # LISTEN wakes only after the row is durably committed.
        async with get_sessionmaker()() as db:
            conn = await db.connection()
            await ensure_inbox_queue(conn, session_id)
            await send_user_message(conn, session_id, run_id, messages)
            await db.commit()

        # Dedup the persister task by (session_id, run_id). Two
        # concurrent POST /stream calls for the same in-flight run must
        # NOT subscribe two persisters onto the same bus topic — that
        # double-counts every event in the execution ring and races two
        # `message_uuid` dicts on the first delta. SSE consumers still
        # get their own queue; only the persister is shared.
        execution = await execution_registry.get_or_create(
            session_id, run_id
        )

        persist_tasks = getattr(request.app.state, "persist_tasks", None)
        if persist_tasks is None or not isinstance(persist_tasks, dict):
            persist_tasks = {}
            request.app.state.persist_tasks = persist_tasks

        key = (session_id, run_id)
        existing = persist_tasks.get(key)
        if existing is None or existing.done():
            persist_task = asyncio.create_task(
                _persist_events(
                    session_id=session_id,
                    session_uuid=session_uuid,
                    run_id=run_id,
                    execution=execution,
                )
            )
            persist_task.add_done_callback(_log_persist_task_result)
            persist_tasks[key] = persist_task
            persist_task.add_done_callback(
                lambda t, k=key, d=persist_tasks: d.pop(k, None)
            )

        async def generator() -> AsyncIterator[dict]:
            async for event in event_bus.consume(session_id, run_id, queue):
                yield event
                if is_run_lifecycle_terminal(event):
                    return

        return make_response(request, generator())
    except BaseException:
        await event_bus.unregister(session_id, run_id, queue)
        raise


@router.get("/sessions/{session_id}/stream")
async def stream_get(
    request: Request,
    session_id: str = Path(...),
    run_id: Optional[str] = Query(None),
    last_event_id: Optional[int] = Query(None, alias="last_event_id"),
):
    """Cursor replay + live tail of an in-flight run."""
    if run_id is None:
        raise HTTPException(status_code=400, detail="run_id required")
    execution = await execution_registry.get(session_id, run_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="unknown execution")

    # Register the live subscriber BEFORE snapshotting the execution ring.
    # Otherwise any event appended between the replay snapshot and a later
    # `subscribe` call would be queued onto no subscriber and silently lost
    # (it leaves the ring window, then `event_bus.publish` finds no queue).
    # Doing a second-pass replay using the highest seq we saw closes the
    # remaining gap for events that were appended to the ring AFTER our
    # first replay but BEFORE the publish that filled our queue.
    queue = await event_bus.register(session_id, run_id)
    try:
        async def generator() -> AsyncIterator[dict]:
            highest_seq = last_event_id
            for entry_dict in execution.replay_from(last_event_id):
                highest_seq = entry_dict["seq"]
                yield entry_dict["event"]
            if execution.status != "running":
                return
            # Second-pass replay covers anything appended to the ring
            # between the first replay and the queue registration.
            for entry_dict in execution.replay_from(highest_seq):
                highest_seq = entry_dict["seq"]
                yield entry_dict["event"]
            async for event in event_bus.consume(session_id, run_id, queue):
                yield event
                if is_run_lifecycle_terminal(event):
                    return

        return make_response(request, generator())
    except BaseException:
        # The generator may never be iterated by the framework if
        # `make_response` itself raises before returning. Discard the
        # queue from `_subs` so it does not leak forever and saturate
        # under publish.
        await event_bus.unregister(session_id, run_id, queue)
        raise


async def _persist_user_message(
    session_uuid: uuid.UUID, messages: list
) -> None:
    user = _extract_user_message(messages)
    if user is None:
        return
    async with get_sessionmaker()() as session:
        repo = MessageRepo(session)
        await repo.append(
            session_id=session_uuid,
            role="user",
            content=_extract_text(user),
            content_parts=(
                user if not isinstance(user.get("content"), str) else None
            ),
            finalize=True,
        )
        await session.commit()


async def _persist_events(
    session_id: str,
    session_uuid: uuid.UUID,
    run_id: str,
    execution,
) -> None:
    """Tap the event bus and drive `TextDeltaDebouncer` so durable
    persistence happens alongside SSE delivery. The ExecutionRegistry
    ring is filled at the JSONL ingress boundary; this coroutine only
    handles message-row persistence.

    Holds one AsyncSession for the coroutine's lifetime and commits per
    delta; opening a fresh session per delta saturated the asyncpg pool
    under N concurrent sessions.
    """
    sessionmaker = get_sessionmaker()
    # AG-UI messageId (str) -> Postgres message UUID for append_delta.
    # Assistant rows are lazily inserted on the first delta.
    message_uuid: dict[str, uuid.UUID] = {}

    async with sessionmaker() as session:
        repo = MessageRepo(session)

        async def _ensure_assistant_row(agui_msg_id: str) -> uuid.UUID:
            if agui_msg_id in message_uuid:
                return message_uuid[agui_msg_id]
            row = await repo.append(
                session_id=session_uuid,
                role="assistant",
                content="",
                finalize=False,
            )
            await session.commit()
            message_uuid[agui_msg_id] = row.id
            return row.id

        async def _append_delta(agui_msg_id: str, delta: str) -> None:
            row_id = await _ensure_assistant_row(agui_msg_id)
            await repo.append_delta(row_id, delta)
            await session.commit()

        async def _finalize(agui_msg_id: str) -> None:
            row_id = message_uuid.get(agui_msg_id)
            if row_id is None:
                return
            await repo.finalize(row_id)
            await session.commit()

        debouncer = TextDeltaDebouncer(
            append_delta=_append_delta, finalize=_finalize
        )

        # Per-event idle timeout. If the runner crashes without emitting
        # a terminal frame and the heartbeat watcher does not synthesize
        # one, parking forever in `event_bus.subscribe` would hold the
        # asyncpg session checked out indefinitely. Cap each await at
        # the heartbeat-timeout window + grace.
        settings = get_settings()
        idle_budget_s = (
            float(getattr(settings, "runner_heartbeat_timeout_s", 30)) + 30.0
        )

        # essential=True: the persister gets an unbounded queue so the
        # slow-subscriber sentinel cannot drop it mid-run. A transient
        # DB stall must not let `event_bus.publish` silently stop
        # persisting while SSE clients keep receiving — that would
        # leave the DB with only a prefix of the run.
        bus_iter = event_bus.subscribe(
            session_id, run_id, essential=True
        ).__aiter__()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        bus_iter.__anext__(), timeout=idle_budget_s
                    )
                except asyncio.TimeoutError:
                    log.warning(
                        "stream.persister_idle_timeout session=%s run=%s "
                        "budget_s=%.1f; runner likely crashed without "
                        "terminal frame",
                        session_id,
                        run_id,
                        idle_budget_s,
                    )
                    return
                except StopAsyncIteration:
                    return
                wire = normalize(event)
                kind = wire.get("type")
                if kind == "TEXT_MESSAGE_CONTENT":
                    await debouncer.on_content(
                        wire["messageId"], wire["delta"]
                    )
                elif kind == "TEXT_MESSAGE_END":
                    await debouncer.on_end(wire["messageId"])
                if is_run_lifecycle_terminal(wire):
                    return
        finally:
            await bus_iter.aclose()


def _log_persist_task_result(task: asyncio.Task) -> None:
    """Done-callback for the fire-and-forget persistence task. Without
    this, exceptions inside `_persist_events` (UNIQUE-violation on
    concurrent inserts, pool-checkout timeout, etc.) disappear into
    asyncio's default exception handler and the assistant message
    ends up empty in the DB with no diagnostic trail."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.exception(
            "stream._persist_events_failed", exc_info=exc
        )


def _extract_user_message(messages: list) -> dict | None:
    if not messages:
        return None
    last = messages[-1]
    if isinstance(last, dict):
        role = last.get("role")
        msg = last
    else:
        role = getattr(last, "role", None)
        msg = (
            last.model_dump() if hasattr(last, "model_dump") else dict(last)
        )
    if role != "user":
        return None
    return msg


def _extract_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # ag-ui InputContent list; pick text parts.
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "".join(parts)
    return ""


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None


async def _build_hydration_payload(
    session_id: str,
    session_uuid: uuid.UUID,
    run_id: str,
) -> HydrationPayload:
    """Construct the HydrationPayload from durable state.

    Loads the full conversation history from messages + last STATE_SNAPSHOT
    from audit_log, and assembles a per-spawn `runner_id` + HMAC secret."""
    settings = get_settings()

    async with get_sessionmaker()() as session:
        messages = await MessageRepo(session).list_for_session(
            session_uuid, after_seq=None, limit=10_000
        )
        prior_messages = [_message_to_dict(m) for m in messages]
        snap = await AuditRepo(session).last_state_snapshot(session_uuid)
        last_state: dict[str, Any] = {}
        if snap is not None and isinstance(snap.payload, dict):
            last_state = snap.payload.get("state") or {}

    base_prompt = (
        "You are a code-intelligence research agent. Use the available "
        "MCP tools to look things up and the `summarizer` sub-agent for "
        "final answers. Cite symbol FQNs verbatim."
    )

    # Agent reaches kloc-intelligence over Streamable HTTP MCP, not via
    # a stdio child. The operator brings up the kloc-intelligence stack
    # (Neo4j + Qdrant + `kloc-intelligence mcp-server-http`) out of
    # band; the agent treats it as an opaque MCP endpoint.
    mcp_url = settings.kloc_mcp_url

    # Provider + model_id come from Settings only. Per-request env reads
    # were a silent-override path that could route a session to a provider
    # the operator never configured a key for; misconfig must surface at
    # boot (the `_validate_provider_key` validator), not inside the runner.
    llm_provider = settings.llm_provider
    model_id = settings.llm_model_id

    return HydrationPayload(
        session_id=session_id,
        run_id=run_id,
        runner_id=str(uuid.uuid4()),
        runner_secret=uuid.uuid4().hex,
        system_prompt=base_prompt,
        model_id=model_id,
        llm_provider=llm_provider,
        prior_messages=prior_messages,
        state=last_state,
        mcp_endpoints=[McpHttpEndpoint(url=mcp_url)],
        skills_dir="/skills",
        backend_url=settings.backend_url,
        heartbeat_interval_s=15,
        pg_dsn=settings.database_url,
        inbox_queue=inbox_queue_name(session_id),
    )


def _message_to_dict(msg: Any) -> dict[str, Any]:
    """Convert ORM Message row to a dict shaped for ag-ui-protocol's
    Message union. The runner re-passes this list into
    `RunAgentInput.messages`."""
    return {
        "id": str(msg.id),
        "role": msg.role,
        "content": msg.content or "",
    }
