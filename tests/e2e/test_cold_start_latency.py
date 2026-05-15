"""E2E Scenario 10 — Cold-start latency (measurement scenario).

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 10.

Measure spawn → first AG-UI event over N samples. Image is pre-pulled, so
this isolates container-create + python-imports + Agent-build + first MCP
list_tools time. Persist samples to
`.claude/feature-team-runs/kloc-agent-poc/scenario_10_<timestamp>.json`.

Assertion: p95 < 10 s (loose sanity guard; arch target is 1–2 s).
AC17 (rehydrated session latency) sets the formal bar.

Milestone gate: M4.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]

EVIDENCE_DIR = Path(
    "/Users/michal/dev/ai/kloc/.claude/feature-team-runs/kloc-agent-poc"
)
SAMPLES = 5
P95_BUDGET_S = 10.0


@pytest.mark.asyncio
async def test_scenario_10_cold_start_latency(
    anthropic_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
):
    """5 samples of POST→first SSE event; record p50/p95/max; assert p95 < 10s."""
    samples_s: list[float] = []

    for i in range(SAMPLES):
        create = await async_http_client.post("/v1/sessions", json={})
        assert create.status_code == 201
        session_id = create.json()["session_id"]

        run_id = str(uuid.uuid4())
        body = {
            "run_id": run_id,
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": "Reply with exactly: COLD_START_OK",
                }
            ],
        }

        t_send = time.monotonic()
        first_event_at: float | None = None
        async with sse_helpers.sse_request(
            async_http_client,
            "POST",
            f"/v1/sessions/{session_id}/stream",
            json=body,
            timeout=60.0,
        ) as stream:
            async for ev in stream:
                if first_event_at is None:
                    first_event_at = time.monotonic()
                if ev.get("type") in ("RUN_FINISHED", "RUN_ERROR"):
                    break

        assert first_event_at is not None, (
            f"sample {i}: no SSE events arrived within 60s — cold start "
            f"regressed catastrophically"
        )
        samples_s.append(first_event_at - t_send)

        # Force-evict the container by waiting past the warm-idle window so
        # the next sample is a true cold spawn. RUNNER_WARM_IDLE_S is 60s
        # in prod; we can't shrink it from outside, so we send-cancel.
        await async_http_client.post(
            f"/v1/sessions/{session_id}/runs/{run_id}/cancel"
        )
        # Brief breath so the warm-idle task settles.
        await asyncio.sleep(0.5)

    samples_s.sort()
    p50 = samples_s[len(samples_s) // 2]
    p95_idx = int(len(samples_s) * 0.95)
    p95 = samples_s[min(p95_idx, len(samples_s) - 1)]
    p_max = samples_s[-1]

    ts = int(time.time())
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = EVIDENCE_DIR / f"scenario_10_{ts}.json"
    out_path.write_text(
        json.dumps(
            {
                "samples_s": samples_s,
                "p50_s": p50,
                "p95_s": p95,
                "max_s": p_max,
                "budget_p95_s": P95_BUDGET_S,
            },
            indent=2,
        )
    )

    print(
        f"\ncold_start (n={SAMPLES}): "
        f"p50={p50:.2f}s p95={p95:.2f}s max={p_max:.2f}s"
    )

    assert p95 < P95_BUDGET_S, (
        f"p95 cold-start {p95:.2f}s > budget {P95_BUDGET_S}s; AC17 risk. "
        f"All samples: {[round(s, 2) for s in samples_s]}"
    )
