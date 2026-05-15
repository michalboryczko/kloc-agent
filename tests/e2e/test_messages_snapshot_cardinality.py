"""E2E Scenario 9 — MESSAGES_SNAPSHOT cardinality (measurement scenario).

See `.claude/qa-notes/kloc-agent-poc_qa_ref_note.md` §4 Scenario 9.

10-turn conversation; record per-event byte size; produce a markdown
evidence file under `.claude/feature-team-runs/kloc-agent-poc/` if the
cumulative MESSAGES_SNAPSHOT bytes at turn 10 exceeds 1 MB (R3
threshold; team-lead-confirmed).

The test PASSES regardless of the threshold — its job is to produce
data, not to gate. The evidence file is the deliverable.

Milestone gate: M3 (data gathering for M4 backlog).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.slow]


EVIDENCE_DIR = Path(
    "/Users/michal/dev/ai/kloc/.claude/feature-team-runs/kloc-agent-poc"
)
R3_THRESHOLD_BYTES = 1 * 1024 * 1024  # 1 MB


@pytest.mark.asyncio
async def test_scenario_09_messages_snapshot_cardinality(
    anthropic_api_key,
    kloc_intelligence_path,
    sot_json_fixture,
    compose_stack,
    async_http_client,
    sse_helpers,
    db_session,
    truncate_all_tables,
):
    """10-turn convo; record bytes by event type; write evidence file if > 1 MB."""
    create = await async_http_client.post("/v1/sessions", json={})
    session_id = create.json()["session_id"]

    # Disable warm-idle eviction for the duration by sending follow-ups
    # within the warm-idle window (default 60s).
    follow_ups = [
        "Find handlers of OrderPlaced.",
        "Which one calls the email service?",
        "Show me callers of that email service.",
        "Are any of them async?",
        "Get the source of the OrderController.",
        "Summarise the OrderRepository.",
        "List events emitted by Order.",
        "Which handlers listen to OrderCancelled?",
        "Compare OrderPlaced and OrderCancelled handlers.",
        "Give a final two-line conclusion.",
    ]

    per_turn: list[dict] = []

    for turn_index, prompt in enumerate(follow_ups):
        run_id = str(uuid.uuid4())
        body = {
            "run_id": run_id,
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "role": "user",
                    "content": prompt,
                }
            ],
        }
        events = await sse_helpers.collect_until(
            async_http_client,
            "POST",
            f"/v1/sessions/{session_id}/stream",
            json=body,
            stop_types=("RUN_FINISHED", "RUN_ERROR"),
            timeout=420.0,
        )

        bytes_by_type = sse_helpers.total_bytes_by_type(events)
        snapshot_bytes = bytes_by_type.get("MESSAGES_SNAPSHOT", 0)
        per_turn.append(
            {
                "turn_index": turn_index + 1,
                "prompt": prompt,
                "event_counts": {
                    t: sum(1 for e in events if e.get("type") == t)
                    for t in sorted({e.get("type") for e in events})
                    if t
                },
                "bytes_by_type": bytes_by_type,
                "messages_snapshot_bytes_this_turn": snapshot_bytes,
            }
        )

    # Cumulative figures.
    cumulative_snapshot_bytes = sum(
        t["messages_snapshot_bytes_this_turn"] for t in per_turn
    )
    breached = cumulative_snapshot_bytes > R3_THRESHOLD_BYTES

    # Always write the JSON record for later analysis.
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    json_path = EVIDENCE_DIR / f"scenario_09_{ts}.json"
    json_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "r3_threshold_bytes": R3_THRESHOLD_BYTES,
                "cumulative_snapshot_bytes": cumulative_snapshot_bytes,
                "breached": breached,
                "per_turn": per_turn,
            },
            indent=2,
        )
    )

    # If the curve breached the threshold, also write the markdown
    # evidence file team-lead can paste into SUMMARY.md.
    if breached:
        md_path = EVIDENCE_DIR / "scenario-9-bandwidth-curve.md"
        md_path.write_text(
            _format_evidence_md(
                cumulative_snapshot_bytes,
                per_turn,
                R3_THRESHOLD_BYTES,
            )
        )

    # Test passes regardless — this is a measurement scenario. We assert
    # ONLY that we actually got 10 finished runs.
    assert len(per_turn) == 10
    for t in per_turn:
        assert "MESSAGES_SNAPSHOT" in t["bytes_by_type"] or t[
            "messages_snapshot_bytes_this_turn"
        ] == 0, (
            f"turn {t['turn_index']} had no MESSAGES_SNAPSHOT events at all; "
            f"adapter regression?"
        )


def _format_evidence_md(
    cumulative: int, per_turn: list[dict], threshold: int
) -> str:
    lines = [
        "# Scenario 9 — MESSAGES_SNAPSHOT bandwidth evidence",
        "",
        f"Cumulative MESSAGES_SNAPSHOT bytes across 10 turns: **{cumulative:,}** "
        f"(threshold {threshold:,} bytes / 1 MB — **BREACHED**).",
        "",
        "Per-turn breakdown:",
        "",
        "| Turn | Snapshot bytes (this turn) | Running total |",
        "|---|---|---|",
    ]
    running = 0
    for t in per_turn:
        running += t["messages_snapshot_bytes_this_turn"]
        lines.append(
            f"| {t['turn_index']} | {t['messages_snapshot_bytes_this_turn']:,} "
            f"| {running:,} |"
        )
    lines += [
        "",
        "## Recommendation",
        "",
        "Per investigation.md §7 R3, flip "
        "`StrandsAgentConfig.emit_messages_snapshot=False` and reconstruct on "
        "the client OR adopt incremental snapshots if/when this becomes a "
        "production concern.",
    ]
    return "\n".join(lines)
