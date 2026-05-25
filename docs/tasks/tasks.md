# kloc-agent — Implementation Tasks

Demo-stability tasks for the runner ↔ backend transport seam plus the
tool-call policy layer. Each task is a separate file under `docs/tasks/`
and follows the USDL template:

```
# T<NN> — <title>
## Status                     — pending | in-progress | passed | blocked
## Spec references            — anchor ids in docs/usdl/*.xml + docs/specs/*.md
## Description                — what to build
## Deliverables               — file paths the task must produce
## How to review              — objective checks the verifier agent must run
## Dependencies               — prerequisite task ids
## Notes                      — open items, precedence ordering, cross-repo links
```

## Execution flow

For each task in dependency order (or in parallel batches per the
dependency graph):

1. **Task agent (`subagent_type: general-purpose` or `feature-implementer`).** Receives the task file content verbatim plus `docs/specs/<slug>.md` + the four `docs/usdl/*.xml` Sections as context. Implements the deliverables. Returns a short summary diff.
2. **Verifier agent (`subagent_type: code-reviewer` or `generalist-qa`).** Receives the task file's "How to review" section + the task agent's diff. Runs every numbered check inside the `<VERIFICATION>` block. Returns `pass | fail` + diagnostics.
3. **On `fail`:** re-spawn the task agent with the verifier's diagnostics appended. Cap at **3 retries** per task. Escalate to the human after the cap.
4. **Record status** in the per-task `## Status` line and in the table below.

The two agents see independent context — the verifier never reads the
task agent's reasoning, only the produced files plus the verification
checks. This matches the `inv.agent-context-isolation` discipline used
in the USDL plugin.

## Tasks

| ID  | Title                                                                                | Status  | Depends on  |
|-----|--------------------------------------------------------------------------------------|---------|-------------|
| T01 | fix-runner-communication: warm-runner reuse hang + oversized-frame channel poisoning | passed-infra-skipped | fix-runner-inbox (closed) |
| T02 | tool-result-size-limits: argument-aware tool policy + actionable hints               | passed-infra-skipped | —           |
| T03 | fix-runner-startup-heartbeat-race: first-heartbeat budget too tight for cold-start MCP init | passed-infra-skipped | —           |
| T04 | agents-autoregistry: load subagents from `agents/<name>/AGENT.md` (replaces hardcoded summarizer) | passed-infra-skipped | —           |

### Predecessor (closed, not numbered)

| Slug                | Title                                          | Status |
|---------------------|------------------------------------------------|--------|
| `fix-runner-inbox/` | PGMQ migration for runner inbox transport      | closed |

The closed `fix-runner-inbox/` lives as a directory under `docs/tasks/`
with its own multi-file structure (problem.md, root-cause.md, decision.md,
acceptance.md, implementation-plan.md, CHANGES.md, README.md, spec/).
That format predates the USDL single-file convention adopted here from
T01 onward. New tasks follow the single-file USDL template.

## Parallelisation hint

- **T01, T02, T03, and T04 are independent.** T02 ships a backend-side
  policy layer; T01 ships transport-layer fixes; T03 ships a
  heartbeat-dispatch fix plus a default-timeout bump; T04 ships a
  subagent autoregistry that replaces the hardcoded summarizer.
  Any can land first; all four can run concurrently against `master`.
- **File ownership across the four tasks has minimal overlap.** T01
  owns `src/shared/`, `src/runner_mgmt/warm_idle.py`,
  `src/runner_mgmt/registry.py`, `src/db/models.py`, and the
  cap-rejection branch of `src/api/internal.py` (~lines 190+) plus
  the offload + permanent-failure paths in `runner/channel.py`. T02
  owns `src/hooks_audit/`, `runner/hooks/audit.py`, and adds fields
  to `src/settings.py`. T03 owns `src/runner_mgmt/heartbeat.py`,
  the `_heartbeat_loop` in `runner/channel.py`, the heartbeat
  dispatch branch of `src/api/internal.py` (~line 109), the
  `runner_heartbeat_timeout_s` default in `src/settings.py`, and
  `.env` / `.env.example`. T04 owns `runner/agents_loader.py` (new),
  the subagent construction block in `runner/agent_factory.py`, the
  `agents_dir` field on `HydrationPayload` and the
  `runner_subagent_load_failed` literal on `AuditEventType` in
  `src/db/models.py`, `build_agents_mount` in
  `src/runner_mgmt/hydrate.py`, the `Mounts` list in
  `src/runner_mgmt/docker_runner.py`, `agents/summarizer/AGENT.md`
  (new), the base prompt string in `src/api/stream.py`, and the
  `agents-init` sidecar + `kloc-agents` volume in `docker-compose*.yml`.
  The USDL Sections are the only shared write surface; each task's
  USDL changes touch disjoint Element ids so a merge is
  non-conflicting (T04 adds a new `cmp.runner.agents-loader` element
  and edits the `cmp.runner.entrypoint` structure block in a region
  T03 does not touch).

## Spec-amendment policy

Unlike the USDL MVP plugin (which uses overlay bundles via `usdl merge`
from T29 onward), kloc-agent does not have an overlay merge tool. Each
task amends `docs/usdl/{behavior,topology,interfaces,composition}.xml`
in place as part of its deliverables. The verifier checks confirm the
amendments landed via `grep -F` against the canonical files.

## Cross-repo dependencies

T02 has one cross-repo deliverable: a `/v1/file_stat` HTTP endpoint
in the `kloc-intelligence` repo. That sibling change is tracked
separately. A fixture stat server (`tests/fixtures/intel_stat_server.py`)
is the contract source of truth in this repo until the real endpoint
lands.

T03 has no cross-repo dependencies.

T04 has no cross-repo dependencies. The `agents/` directory and
`kloc-agents` named volume are local to this repo.

## Open Items roll-up

PM decisions blocking task closure are listed in each task's
`## Notes` section. The roll-up below names which task each open item
belongs to:

- **T01:** 429-status classification (permanent vs transient);
  terminal-frame `RUN_ERROR` synthesis policy; artifact MIME for
  offloaded AG-UI payloads; whether AC1's `< 200 ms` enqueue bound
  becomes an `<nfr>` under `beh.ask-assistant`.
- **T02:** `file_stat` MCP-tool exposure (in addition to the private
  HTTP endpoint); ship-with-defaults vs ship-empty for
  `KLOC_TOOL_LIMITS`; hint copy review; whether to add a `<rule>`
  under `beh.ask-assistant` for "tool denials carry an actionable
  hint the agent observes as the tool result"; audit payload schema
  versioning for the new `tool_limit:*` reason namespace.
- **T03:** whether to tighten the 60 s default once
  `kloc_agent.runner.spawn_to_first_beat_s` telemetry stabilises;
  whether to extend the first-beat frame with structured
  `mcp_init_ms` / `agent_build_ms` timings so operators can drill
  into cold-start regressions; whether the `INJECT_RUNNER_INIT_DELAY_S`
  test fixture should remain in the runner image after T03 closes
  or move into a test-only Dockerfile overlay.
- **T04:** per-subagent tool filtering via frontmatter `tools:`
  allowlist (deferred until a specialist demonstrably suffers from
  the full 22-tool MCP surface); per-subagent model override via
  frontmatter `model_id:` (deferred until cost telemetry justifies);
  whether subagent intermediate events propagate through the
  orchestrator's AG-UI stream (empirical check on a non-summarizer
  subagent); migration path to Strands `graph` orchestration once
  ≥3 deterministically-routed subagents land.

None of the open items block the verification checks listed in each
task's `<VERIFICATION>` block. They surface during the task's review
cycle and either resolve before close or land as follow-ups.
