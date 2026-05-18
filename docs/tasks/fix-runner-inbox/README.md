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

## Status

- Investigated: 2026-05-19
- Decision: **PGMQ for the inbox channel** — see [decision.md](./decision.md)
- Implementation: not started — see [implementation-plan.md](./implementation-plan.md)
- Acceptance: see [acceptance.md](./acceptance.md)
