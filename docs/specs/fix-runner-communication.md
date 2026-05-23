# Feature Spec: fix-runner-communication

## Problem

Two distinct runner ↔ backend communication bugs survive the PGMQ inbox migration. They share one user-visible symptom — "the chat freezes" — but have independent root causes and independent fixes. Both must close before the runner channel is considered demo-stable.

### Bug 1 — warm-runner reuse self-blocks for the full warm-idle window

After a Session completes its first turn, the warm runner sits inside `RUNNER_WARM_IDLE_S` (default 300 s). When the Analyst submits a second message during that window, `src/api/stream.py:stream_post` persists the user row, then calls `RunnerRegistry.get_or_spawn(...)`. `get_or_spawn` finds the warm `RegistryEntry` and calls `entry.warm_idle.await_kill_in_flight()` at `src/runner_mgmt/registry.py:222`. That method awaits `self._task` at `src/runner_mgmt/warm_idle.py:47-57`. `self._task` is the `_await_idle_then_kill` countdown, which is sitting in `await asyncio.wait_for(self._activity.wait(), timeout=warm_idle_s)`.

The `activity` event would be set by `WarmIdleManager.on_user_message()` — but `stream_post` only calls that *after* `get_or_spawn` returns. The call self-blocks. The handler stays inside `get_or_spawn` until either the warm-idle timeout expires (300 s default), at which point the manager terminates the container, the entry is removed, and `get_or_spawn` falls through to spawn a fresh runner; or an operator kills the container by hand.

Confirmed by DB timestamps on session `068e7f09-1f4a-4c8b-973e-c11c131d2531`: user row INSERT at 21:23:15.641 (server-side `now()` per `src/db/models.py:166-168`); no further activity until `runner_warm_idle_evicted` at 21:28:13; new runner spawned and the message processed thereafter.

`docs/tasks/fix-runner-inbox/` ("second message does nothing") fixed a different root cause — the in-memory `asyncio.Queue` identity drift between `RegistryEntry` spawns. The symptom name was recycled. The bug described here is post-PGMQ and structurally separate.

### Bug 2 — oversized AG-UI frame poisons the runner channel

`src/api/internal.py:197` enforces `MAX_LINE_BYTES = 1 * 1024 * 1024` on every JSONL line of `POST /internal/sessions/{id}/events`. When the runner emits an AG-UI event whose serialisation exceeds 1 MiB — typically a `ToolCallResult` carrying full `kloc_flows` or `file_read` output — the backend responds **413 Request Entity Too Large** and closes the connection.

`runner/channel.py:_stream_outbound` reacts to *any* 4xx/5xx by prepending every frame yielded during the broken attempt back into `pending_after_break` (the inline comment justifies it as "over-delivery is preferable to silent loss"), sleeping a backoff, and reconnecting. The next attempt's body iterator re-yields the same oversized frame first, the backend rejects 413 again, the channel reconnects again, ad infinitum. The persister stops seeing events, hits its 60 s idle budget (`stream.persister_idle_timeout`), the heartbeat watcher eventually fires `runner_heartbeat_lost`, the runner is killed. User-visible: the "ultimate long response" terminates the runner instead of producing a reply.

Confirmed in the same audit-log capture: two consecutive `413` responses on the runner's events POST, then `persister_idle_timeout`, then `runner_heartbeat_lost`.

Both bugs are at the same architectural seam — the runner ↔ backend transport — and both stem from missing or wrong failure handling on deterministically-rejected operations. Bug 1 has the wrong primitive blocking on the wrong condition. Bug 2 has a blanket "replay everything" policy that cannot recover from a permanent reject. They land in one spec because they are the two halves of "the runner cannot talk".

## Decision

Two structurally independent fixes, one spec.

**Bug 1 fix — split `WarmIdleManager` lifecycle into countdown vs terminating phases.** `await_kill_in_flight` returns immediately during the countdown phase. It only blocks once the activity-wait has timed out and the manager has begun calling `Runner.terminate(handle)`. The new boolean `_killing: bool` is the gate. Additionally, `RunnerRegistry.get_or_spawn`'s warm-runner reuse path calls `entry.warm_idle.on_user_message()` *before* `await_kill_in_flight()` so cancellation of the countdown task is atomic with the reuse decision; concurrent stream POSTs against the same Session lose the spawn-lock race and serialise behind it as today.

**Bug 2 fix — split outbound-channel failure handling into transient vs permanent, and bound the frame size at source.** Transient failures (connection drop, 5xx, 408) keep today's prepend-and-retry behaviour. Permanent failures (any other 4xx) drop the offending frame, emit a runner-side `RUN_ERROR` AG-UI event with `cause: "frame_rejected"` and the upstream status code, and continue draining the queue. The backend cap is raised to `MAX_LINE_BYTES = 16 MiB` as defence-in-depth. The runner enforces `RUNNER_MAX_FRAME_BYTES = 15 MiB` before enqueueing; oversized payloads (typically large tool results) are routed through the existing `ArtifactRegistered` HMAC webhook + S3 path and the AG-UI frame is rewritten to a `ToolCallResult` whose body references the artifact id. The runner can therefore never produce a frame its own backend would reject.

The two constants (`MAX_LINE_BYTES` backend, `RUNNER_MAX_FRAME_BYTES` runner) are imported from a single shared source so they cannot drift.

## Acceptance Criteria

### Bug 1 — warm-runner reuse no longer hangs

**AC1.**
```gherkin
Scenario: second message into a warm runner is enqueued promptly
  Given a Session that has just completed a successful first turn
    And the warm-idle countdown is mid-flight (within RUNNER_WARM_IDLE_S)
  When the Analyst submits a second message
  Then send_user_message (pgmq.send + NOTIFY) is called within 200 ms p95
       of POST /v1/sessions/{id}/stream returning SSE-open
    And the runner picks up the message from PGMQ within 1 s of submission
    And no warm-idle eviction fires for this Session as a side effect
```

**AC2.** Unit test on `WarmIdleManager`: drive the manager to the countdown phase, call `await_kill_in_flight()`, assert it returns within 10 ms. Drive the manager past the activity-wait timeout into the terminate phase, call `await_kill_in_flight()`, assert it awaits the in-flight terminate.

**AC3.** Unit test on `RunnerRegistry.get_or_spawn`: with a warm entry present, assert `entry.warm_idle.on_user_message()` is called before `entry.warm_idle.await_kill_in_flight()`. Use a fake `Runner` so the test does not require Docker.

**AC4.** Regression test `tests/integration/test_warm_runner_second_message.py` that would have caught the bug: complete turn 1 via a real PGMQ round-trip, wait for `RUN_FINISHED`, immediately submit turn 2, assert the second `pgmq.send` row is visible in `pgmq.q_inbox_<slug>` within 500 ms.

### Bug 2 — oversized frame no longer poisons the channel

**AC5.**
```gherkin
Scenario: a tool result over the channel cap is offloaded to artifacts
  Given the agent calls a tool whose serialised result is 24 MiB
  When the runner is about to enqueue the ToolCallResult AG-UI frame
  Then the runner uploads the 24 MiB payload to S3 via the existing
       ArtifactRegistered webhook path
    And the AG-UI frame the runner emits is a ToolCallResult whose
        content body references artifactId only
    And the frame is < RUNNER_MAX_FRAME_BYTES
    And the backend accepts the frame (no 413)
    And the FE receives the ToolCallResult plus the ArtifactAttached
        CUSTOM event for inline rendering
```

**AC6.**
```gherkin
Scenario: a permanently rejected frame does not poison the channel
  Given the runner emits a synthetic AG-UI frame the backend deterministically
        rejects with HTTP 422 (schema invalid)
  When the channel observes the 4xx
  Then the offending frame is removed from pending_after_break
    And a single RUN_ERROR AG-UI event with cause="frame_rejected" and
        upstream_status=422 is emitted on the next reconnect
    And subsequent queued frames are delivered normally
    And the channel reconnects at most once for the rejected frame
```

**AC7.** Constants:
- `src/api/internal.py`: `MAX_LINE_BYTES = 16 * 1024 * 1024`.
- `runner/channel.py`: `RUNNER_MAX_FRAME_BYTES = 15 * 1024 * 1024`.
- Both imported from `src/shared/transport_limits.py` (new module) so a single edit changes both. Grep verification: `MAX_LINE_BYTES` and `RUNNER_MAX_FRAME_BYTES` are each defined exactly once.

**AC8.** `runner/channel.py:_stream_outbound` distinguishes:
- Transient: `httpx.TransportError`, response status 408, 500-599 → existing prepend-and-retry.
- Permanent: response status in {400, 413, 414, 415, 422} → drop offending frame, emit `RUN_ERROR`, do not prepend.

**AC9.** A new helper `runner/channel.py:_offload_oversize_frame(frame)` is called from the runner-side enqueue path; it short-circuits to the artifact-upload path when `len(serialised) > RUNNER_MAX_FRAME_BYTES`. Verified by unit test using a 16 MiB synthetic tool result.

### Audit + observability

**AC10.** A new audit event `runner_channel_frame_rejected` is appended to the locked vocabulary in `src/db/models.py:AuditEventType`. Payload shape: `{runner_id, run_id, cause: "frame_rejected"|"frame_oversized", upstream_status: int, byte_size: int}`. Emitted exactly once per dropped frame.

**AC11.** OTel counter `kloc_agent.runner.frame_rejected_total{cause=...}` and histogram `kloc_agent.runner.frame_bytes` are emitted by the JSONL ingress at the cap-check boundary so frame-size distribution and rejection rate are visible without parsing logs.

### Tests

**AC12.** New tests pass:
- `tests/unit/test_warm_idle_phases.py` (AC2).
- `tests/unit/test_registry_warm_runner_reuse_ordering.py` (AC3).
- `tests/integration/test_warm_runner_second_message.py` (AC4).
- `tests/unit/test_channel_permanent_failure.py` (AC6, AC8).
- `tests/unit/test_channel_offload_oversize.py` (AC9).
- `tests/integration/test_large_tool_result.py` end-to-end with a 24 MiB synthetic tool result (AC5).

**AC13.** Existing suite (`pytest tests/ -q`) remains green.

### Documentation

**AC14.** `docs/usdl/composition.xml`:
- `cmp.backend.runner-mgmt`: the structure description of `RegistryEntry` no longer references the deleted `inbox` field (stale post-PGMQ).
- New rule under `cmp.backend.runner-mgmt`: *"`get_or_spawn`, when reusing an existing entry, calls `warm_idle.on_user_message()` before `await_kill_in_flight()`."*
- New constraint `con.warm-idle-cancel-before-reuse` formalises the ordering.
- `con.channel-replay-on-failure` is replaced by two constraints: `con.channel-replay-on-transient` (5xx, 408, transport drop) and `con.channel-drop-on-permanent` (other 4xx → drop + RUN_ERROR).
- New rule under `cmp.runner.channel`: *"The runner refuses to enqueue any JSONL line larger than `RUNNER_MAX_FRAME_BYTES`; oversized payloads are routed through the artifact-upload path."*

**AC15.** `docs/usdl/interfaces.xml`:
- `ifc.ingest-runner-events.failure-mode condition="oversized-frame"` gains `caller-action="drop-and-emit-run-error"`.
- The 16 MiB cap is named in the contract intent.

**AC16.** `docs/usdl/topology.xml`:
- `top.com.runner-to-backend-events` note documents the 16 MiB cap explicitly.

**AC17.** `docs/tasks/fix-runner-inbox/README.md` adds a note that the "second message does nothing" symptom now has a second root cause covered here, so the prior task is not misread as the active fix.

## Non-Goals

- Streaming partial AG-UI frames over multiple JSONL lines (would change the AG-UI wire format — out of scope).
- Per-tool size caps enforced *before* the tool is invoked — see `tool-result-size-limits.md` for that layer.
- Generalising replay-vs-drop semantics to non-JSONL transports.
- Multi-worker uvicorn safety — single-worker invariants remain.
- Backpressuring the agent (e.g. denying further tool calls when the channel is saturated).
- Migrating in-flight Sessions across a deploy — clean cut; any mid-flight oversized frame is by definition dropped on rollout.
- Tightening the 16 MiB cap based on real measurements; this spec sets the cap, follow-up work tunes it.

## Open Items (require PM resolution before implementation closes)

1. **Permanent-4xx status list.** This spec lists `{400, 413, 414, 415, 422}` as deterministically permanent. Is 429 permanent (rate-limited frame is no different next attempt) or transient (retry after delay)? PM/architect decision needed.
2. **`RUN_ERROR` emission for dropped frames mid-run.** When the rejected frame is itself a terminal lifecycle frame (`RUN_FINISHED`, `RUN_ERROR`), should the channel still emit a synthetic `RUN_ERROR` (risking duplicate terminal frame) or silently drop and let the heartbeat watcher fire? Recommend: synthesise; the AG-UI consumer already tolerates duplicate intermediates.
3. **Artifact MIME for offloaded AG-UI payloads.** Store as `application/json` with `.json` extension, or as a custom `application/x-agui-frame` so the FE can distinguish from analyst-uploaded JSON artifacts? Recommend: custom MIME, so the FE renders an inline tool-result chip rather than a generic file chip.
4. **Behavior.xml updates.** Bug 1 produces a user-visible behavior change ("second message responds promptly"); Bug 2 is a reliability/operational fix. Should AC1's < 200 ms enqueue bound be captured as an `<nfr>` under `beh.ask-assistant`? Recommend: yes for AC1, no for the rest.
