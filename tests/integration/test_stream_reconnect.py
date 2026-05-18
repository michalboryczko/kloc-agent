"""Integration tests for SSE reconnect / dedup invariants (ISS-02).

Verifies that two concurrent POST /v1/sessions/{id}/stream requests for
the same in-flight (session_id, run_id):

  * spawn exactly one `_persist_events` task in
    `app.state.persist_tasks[(session_id, run_id)]`
  * do NOT double-append events to the `execution_registry` ring
    (single Execution, each runner-emitted event recorded exactly once)

Pre-fix code unconditionally created a fresh persister on every POST,
so the second concurrent caller subscribed a second persister onto the
same bus topic and doubled the ring.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from src.streaming.execution_registry import execution_registry
from tests.conftest import FakeRunner

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_concurrent_post_stream_same_run_id(
    asgi_client, app_in_process, truncate_all_tables, monkeypatch
):
    """ISS-02: two concurrent POST /stream for the same (sid, run_id)
    produce exactly one persister and one execution ring (no doubling).

    Approach: install a counter around `_persist_events` so we can
    assert spawn-count at runtime, fire the scripted runner events
    while both POSTs are still consuming the SSE body (so RUN_FINISHED
    closes their generators), then assert the execution-registry ring
    contains each event exactly once.
    """
    fake_runner = FakeRunner()
    app_in_process.state.runner_registry.set_runner(fake_runner)

    create = await asgi_client.post("/v1/sessions", json={})
    assert create.status_code == 201, create.text
    sid = create.json()["session_id"]

    run_id = "rA-" + uuid.uuid4().hex[:8]
    body = {
        "runId": run_id,
        "messages": [{"role": "user", "content": "hi"}],
    }
    url = f"/v1/sessions/{sid}/stream"

    # Wrap the real `_persist_events` so we can count how many times
    # stream_post spawns it. Pre-fix code spawned it twice for the two
    # concurrent POSTs; the dedup'd code spawns it exactly once.
    from src.api import stream as stream_mod

    spawn_count = {"n": 0}
    original_persist = stream_mod._persist_events

    async def _counting_persist(**kwargs):
        spawn_count["n"] += 1
        return await original_persist(**kwargs)

    monkeypatch.setattr(stream_mod, "_persist_events", _counting_persist)

    async def _publish_scripted_after(delay: float) -> None:
        """Schedule the scripted runner event sequence after both POSTs
        have wired their SSE subscribers onto the bus. Frames flow
        through `_dispatch_frame` so the ExecutionRegistry ring is
        populated at the same boundary the production JSONL ingress
        uses — the SSE generator and the persister both see frames
        already stamped with seq."""
        await asyncio.sleep(delay)
        scripted = [
            {"type": "RUN_STARTED", "runId": run_id},
            {
                "type": "TEXT_MESSAGE_CONTENT",
                "messageId": "m1",
                "delta": "hi",
            },
            {"type": "RUN_FINISHED", "runId": run_id},
        ]

        class _Req:
            app = app_in_process

        from src.api.internal import _dispatch_frame

        for evt in scripted:
            await _dispatch_frame(_Req(), sid, evt)

    publisher = asyncio.create_task(_publish_scripted_after(delay=0.3))

    # Two concurrent POSTs. Each blocks on its SSE generator until
    # RUN_FINISHED is dispatched, then returns 200 with the assembled
    # SSE body.
    try:
        responses = await asyncio.wait_for(
            asyncio.gather(
                asgi_client.post(url, json=body),
                asgi_client.post(url, json=body),
            ),
            timeout=20.0,
        )
    finally:
        if not publisher.done():
            publisher.cancel()
            try:
                await publisher
            except (asyncio.CancelledError, Exception):
                pass

    assert all(r.status_code == 200 for r in responses), [
        (r.status_code, r.text) for r in responses
    ]
    # The minimal scripted events do not carry every AG-UI field needed
    # for the strict encoder, so the wire body may drop frames; the
    # important invariant is that both generators ran to terminal (the
    # gather above returned) — `is_run_lifecycle_terminal` inspects the
    # raw dict regardless of encoder validation.

    # Persister spawn count: exactly one for two concurrent POSTs.
    assert spawn_count["n"] == 1, (
        f"expected _persist_events to be spawned once, got "
        f"{spawn_count['n']} (pre-fix would be 2)"
    )

    # The persister has completed and removed itself from persist_tasks
    # by now (done-callback pops the entry). The execution_registry
    # ring is the durable evidence: each scripted event should appear
    # exactly once, not twice.
    execution = await execution_registry.get(sid, run_id)
    assert execution is not None, "execution should exist after publish"
    assert len(execution.events) == 3, (
        f"expected 3 events on the ring (no double-count), "
        f"got {len(execution.events)}: "
        f"{[e['event'].get('type') for e in execution.events]}"
    )
