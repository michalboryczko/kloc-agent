# fix-runner-inbox

Replace the in-memory long-poll inbox between backend and runner with a
durable broker-backed transport.

The bug investigated below — the "second message does nothing" symptom —
is one specific manifestation of a wider category of failures the current
transport admits. The remediation is **transport replacement**, not a
patch on the long-poll loop.

## Documents in this task

- [problem.md](./problem.md) — observed symptom, reproduction, evidence
  from logs / DB / audit log, root-cause analysis. Read this first.
- [root-cause.md](./root-cause.md) — the underlying class of bug
  (per-spawn inbox-queue identity drift + lost-on-eviction messages)
  and why patching long-poll is not enough.
- [solution-options.md](./solution-options.md) — full set of considered
  alternatives (reverse-SSE, full-duplex HTTP, WebSocket, ZeroMQ, PGMQ,
  RabbitMQ) with trade-offs.
- [decision.md](./decision.md) — chosen approach and rationale.
- [implementation-plan.md](./implementation-plan.md) — staged
  implementation, file-by-file, with deletions called out explicitly
  (no backward compatibility).
- [acceptance.md](./acceptance.md) — acceptance scenarios that must pass
  before this task is considered closed.
- [CHANGES.md](./CHANGES.md) — USDL spec diff catalogue with XPath-style
  selectors, drivers, cross-section reference verification, and open items.
- [spec/](./spec/) — USDL spec drafts (topology / interfaces /
  composition) reflecting the post-task state. Forked from
  `docs/usdl/{topology,interfaces,composition}.xml` and promoted back
  into `docs/usdl/` during implementation.

## Final artefacts

- Feature spec: [docs/specs/fix-runner-inbox.md](../../specs/fix-runner-inbox.md) (17 ACs)
- Executable plan: [docs/specs/fix-runner-inbox-plan.md](../../specs/fix-runner-inbox-plan.md) (T01..T20 + T19b)
- QA reference note: [.claude/qa-notes/fix-runner-inbox_qa_ref_note.md](../../../.claude/qa-notes/fix-runner-inbox_qa_ref_note.md)
- Verification sign-off: `/Users/michal/dev/ai/kloc/.claude/verification-summaries/fix-runner-inbox-full-20260519-084119-summary.md`

## Status

- Investigated: 2026-05-19
- Decision: **PGMQ for the inbox channel** — see [decision.md](./decision.md)
- Implementation: **Closed** (2026-05-19) — 16 atomic commits on `master`, `7465f784e..1342e3289`
- Acceptance: **PASS** — see QA sign-off above. Functional + structural + regression ACs all met. Bug class structurally eliminated.

### Symptom-twin: post-PGMQ "second message does nothing"

After this task closed, the same user-visible symptom resurfaced from a
different root cause: `RunnerRegistry.get_or_spawn` was awaiting the
warm-idle countdown task before forwarding the user message, so the
handler self-blocked for the full warm-idle window. That regression is
fixed under task **T01 — fix-runner-communication** (see
[../T01.md](../T01.md) and
[../../specs/fix-runner-communication.md](../../specs/fix-runner-communication.md)),
which also addresses an unrelated oversized-frame channel-poisoning
bug. Do NOT mistake this task for the active fix — the PGMQ inbox is
working; T01 is the live remediation.

## Follow-up items

Tracked for a future iteration; none of these blocked closing this task.

1. **AC4 automated perf harness.** Add
   `tests/integration/test_inbox_perf.py::test_wake_latency_p95_under_50ms`
   (N=100, warm-up 10, p95 over latter 90, reports median + p99).
   Defends against a wake-latency regression to e.g. 200 ms that
   would still pass AC1's 1 s gate.
2. **AC11 caplog assertion.** Add
   `tests/unit/test_lifespan_boot.py::test_lifespan_logs_pgmq_extension_ready`
   to pin the literal `pgmq extension ready` log line against operator-runbook drift.
3. **AC13 — wire `session.close` to `drop_inbox_queue`.** Helper exists
   at `src/messaging/pgmq.py` but has no production caller. Pick a hook
   point (likely `DELETE /v1/sessions/{id}` or session-state transition
   to `closed`).
4. **behavior.xml — invariants/NFRs for new durability guarantees.**
   Capture AC2 / AC3 / AC4 as `<invariant>` and `<nfr>` blocks under
   `beh.ask-assistant` (deferred per PM, open-item #3 of feature spec).
5. **kloc-agent verification checkpoints.** Add a
   `checkpoints-kloc-agent.json` list to the `verification-checkpoints`
   skill — the existing `checkpoints-full.json` targets the
   scip-php → mapper → cli pipeline only.
6. **Tighten `tests/conftest.py` skip-token breadth.** Reviewer-1 mini-
   review nit on `c047fba10`: the standalone `"extension"` token is
   broader than needed. Narrow to `"pgmq"` only or `"extension \"pgmq\""`
   to avoid future false-skips.
