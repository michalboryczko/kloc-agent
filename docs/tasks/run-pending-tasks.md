# Prompt: Run pending tasks from docs/tasks/tasks.md (dev + strict verify loop)

A self-contained orchestration prompt the main Claude session can run to
implement every pending task under `docs/tasks/`. For each task, spawns
a dev subagent to implement the deliverables, then spawns a strict
verifier subagent to mechanically check the work against the task's
`<VERIFICATION>` block plus `git diff`. Passes only when every check is
demonstrably satisfied; loops on failure up to 3 times; escalates after.

Adapted from `usdl/mvp/commands/run-pending-tasks/PROMPT.md`. Key
differences:
- Paths target `docs/tasks/` and `docs/specs/` rather than `mvp/tasks/` and `mvp/features/`.
- Spec amendments land directly in `docs/usdl/*.xml` (no `usdl merge` tool — kloc-agent has no overlay merge surface).
- Validation runs the kloc-agent test suite (`pytest tests/ -q`) and `docker compose up` smoke for end-to-end checks.
- A closed predecessor (`docs/tasks/fix-runner-inbox/`) lives as a multi-file directory and MUST be ignored — only the numbered `T<NN>.md` tasks in the index are eligible.

## How to invoke

Paste this entire file into a fresh Claude session as the user prompt,
or copy the relevant sections into the active session. The orchestrator
(the main Claude) reads `docs/tasks/tasks.md`, picks the next pending
task in dependency order, and runs the loop below. The orchestrator is
NOT a subagent — it stays in the main session so the user can
interrupt, inspect, or amend at any point.

Set `MAX_RETRIES=3`, `MAX_TASKS=<choose>` (set to `1` to run a single
task; omit to drain the entire pending queue).

## Inputs the orchestrator MUST read

1. **`docs/tasks/tasks.md`** — the index. Filter rows where `Status` column is `pending`. Topo-sort by the `Depends on` column; never start a task whose dependencies are unsatisfied (not all `passed` predecessors). The predecessor `fix-runner-inbox/` is the closed multi-file directory and is NOT in the table — never attempt it.
2. **`docs/tasks/T<NN>.md`** — the task spec for each pending task.
3. **`docs/specs/<slug>.md`** — the feature design doc. Each task's `## Spec references` section names the relevant spec file (e.g. `docs/specs/fix-runner-communication.md` for T01); the orchestrator reads it and passes it verbatim into the dev's payload.
4. **`docs/usdl/{behavior,topology,interfaces,composition}.xml`** — the canonical USDL Spec. Tasks amend these in place as part of their deliverables; the verifier checks `grep -F` patterns against the post-change file state.
5. **`CLAUDE.md` at the repo root** — for the project's coding conventions (Python 3.12, asyncpg, comment policy, audit-vocabulary discipline, single-uvicorn-worker invariants).

## Orchestration loop (pseudocode)

```
tasks_pending = parse_tasks_md_pending_in_topo_order()
processed_count = 0

for task in tasks_pending:
    if processed_count >= MAX_TASKS: break
    if any predecessor in task.depends_on is NOT 'passed': skip with warning

    spec_doc = locate_spec_for_task(task)   # grep the task .md for docs/specs/*.md references

    attempt = 1
    while attempt <= MAX_RETRIES:
        dev_result = spawn_dev_agent(task, spec_doc, prior_diagnostics=verifier_diagnostics if attempt > 1 else None)
        if dev_result.errored:
            escalate("dev agent errored")
            break

        verifier_result = spawn_verifier_agent(task, spec_doc, dev_result)
        if verifier_result.verdict == 'pass':
            update_tasks_md_status(task, 'passed')
            report_success(task)
            break
        else:
            verifier_diagnostics = verifier_result.diagnostics
            attempt += 1
            if attempt > MAX_RETRIES:
                escalate("retry cap reached", task, verifier_diagnostics)
                break

    processed_count += 1

print_final_report()
```

## Dev agent — exact spawn prompt

Use `Agent` tool with `subagent_type: "general-purpose"`. The prompt
below is the COMPLETE prompt — paste verbatim, substituting the
bracketed placeholders.

```
You are a developer implementing one kloc-agent task. You have access to all tools (Read, Write, Edit, Bash, etc.). You will produce code; you will NOT update the task's Status line in docs/tasks/tasks.md (the orchestrator does that). You will NOT commit to git (leave the working tree dirty so the verifier can inspect changes via git diff). You will NOT open a pull request.

## Your task

Read `[ABSOLUTE PATH TO docs/tasks/T<NN>.md]` IN FULL. Pay particular attention to:
- `## Description` — what you are building.
- `## Deliverables` — the exact file paths you must produce.
- `## Spec references` — the spec doc and USDL ids your code must satisfy.

## Required reading (MUST_READ)

Before writing any code, read these files in full:
- `[ABSOLUTE PATH TO docs/specs/<slug>.md]` — the spec doc the task realises (Problem, Decision, Acceptance Criteria). Treat the spec's ACs as ground truth; the task's `<VERIFICATION>` block is the mechanical check against them.
- `docs/usdl/{behavior,topology,interfaces,composition}.xml` — the canonical USDL Spec. Your code must conform; your USDL amendments land directly here (no overlay tool).
- The current state of every file under `## Deliverables`. Even when you are creating a new file, read the parent directory and any sibling modules to copy their patterns.
- `CLAUDE.md` at the repo root — for the project's coding conventions and constraints (Python 3.12 + asyncpg, single uvicorn worker, audit-vocabulary discipline, comment policy from ISS-13, no `os.environ` reads outside `src/settings.py`).
- `docs/tasks/fix-runner-inbox/` — the closed predecessor; read its `decision.md` + `root-cause.md` for context on the PGMQ inbox migration that T01 builds on. Do NOT re-implement anything in that directory.

[IF prior_diagnostics is non-empty:]
## Diagnostics from previous attempt

The verifier rejected your previous attempt with these diagnostics. Address each one. The verifier will re-run the same checks:

[PASTE prior_diagnostics VERBATIM]

## Discipline

- Implement EVERY file under `## Deliverables`. Missing a file is an automatic verifier failure.
- Use existing patterns. The repo's CLAUDE.md and the spec doc name specific patterns (Pydantic BaseSettings for config, AuditEventType Literal for new audit events, `log = logging.getLogger(__name__)` at module level, `async with engine.begin() as conn:` for connection-level operations, `await session.flush()` not `commit()` in repos). Copy them, don't invent.
- Run validation commands when your code is at a point where it should pass:
  - `pytest tests/ -q` — the full Python suite. Must exit 0.
  - `pytest tests/<targeted-path> -q` — for the specific new tests you wrote.
  - `python -c "from src.settings import get_settings; s = get_settings(); ..."` — boot the settings model when you add new fields.
  - When the task changes the FE: `cd frontend && npm run lint && npm run build` — both must exit 0.
  Don't claim done if any validation command fails.
- No half-finished code. If a deliverable is too large, split into smaller units within the same task scope rather than stubbing.
- Don't add comments explaining WHAT (well-named code already does that). Don't add a comment unless the WHY is non-obvious — a workaround, an invariant, a subtle constraint. Never reference task ids, AC numbers, or review rounds in comments (CLAUDE.md ISS-13 policy).
- Honour the comment policy in CLAUDE.md verbatim: no `# T01`, no `# AC5`, no `# Reviewer-2 follow-up`, no person names. Concurrency invariants near locks are encouraged.
- The audit-event vocabulary in `src/db/models.py:AuditEventType` is locked. Adding a new event requires a corresponding write site AND a downstream consumer (otherwise it is dead audit weight). The Description says when this applies.
- The settings layer is the single source for runtime config (`con.settings-no-runtime-env-reads`). Never call `os.environ` from app code; add a field to `Settings` instead.

[IF the task touches the runner ↔ backend transport (T01 or anything in `runner/channel.py` / `src/api/internal.py`):]
- The runner is a separate process inside a separate container. Code in `runner/` may import from `src.db.models` ONLY for `HydrationPayload` (the documented try/except fallback). Do not bring in other backend modules; do not assume in-process state is shared.
- Single uvicorn worker invariant holds. In-process singletons (`event_bus`, `execution_registry`, `runner_registry`) remain process-local. Don't introduce horizontal-scaling assumptions.

[IF the task touches the hooks_audit / policy layer (T02 or anything in `src/hooks_audit/`):]
- The `BeforeToolCall` webhook is HMAC-signed by the runner. The constant-time HMAC primitive (`hmac.compare_digest`) is required (`con.hmac-constant-time`). Don't loosen the verification path.
- New evaluators must be pure decision logic. I/O lives in `stat_client.py` or its peers; the evaluator orchestrates but does not own the socket.
- Fixture stat server at `tests/fixtures/intel_stat_server.py` is the contract source of truth for `ifc.intel-stat` until kloc-intelligence lands the real endpoint. Do NOT push commits to the kloc-intelligence repo — the cross-repo deliverable in T02 is tracked separately.

[IF the task changes USDL Sections under docs/usdl/:]
- Edit the canonical files directly. There is no overlay tool. Use `Edit` (not `Write`) to preserve surrounding sections.
- After your edits, the four files must remain parseable XML. Run a quick `python -c "import xml.etree.ElementTree as ET; [ET.parse(p) for p in ['docs/usdl/behavior.xml','docs/usdl/topology.xml','docs/usdl/interfaces.xml','docs/usdl/composition.xml']]"` to confirm.
- Verifier checks will `grep -F` for the new constraint ids / contract ids; place them precisely where the task names.

## Output

When done, output a structured summary as your final message (and ONLY your final message — no narration above this):

```
SUMMARY
files_written:
  - path: relative/path/from/repo/root.py
    purpose: one line
  - ...
validations_run:
  - cmd: "pytest tests/ -q"
    exit: 0
    summary: "247 passed in 12.4s"
  - cmd: "python -c 'import xml.etree.ElementTree as ET; [ET.parse(p) for p in [...]]'"
    exit: 0
  - cmd: "cd frontend && npm run lint"
    exit: 0    # only if frontend touched
  - ...
notes: short prose, optional
```
```

## Verifier agent — exact spawn prompt

Use `Agent` tool with `subagent_type: "general-purpose"`. The prompt is
INTENTIONALLY strict; copy verbatim.

```
You are a STRICT verifier checking that one kloc-agent task was correctly implemented. You have access to all tools (Read, Bash, Grep, etc.). You will NOT modify files. You will NOT commit. You will produce a single structured verdict.

## Your task

Read `[ABSOLUTE PATH TO docs/tasks/T<NN>.md]` IN FULL. The authoritative checklist is the content INSIDE the `<VERIFICATION>...</VERIFICATION>` tags. Every numbered check inside that block is mandatory.

## Required reading

- `[ABSOLUTE PATH TO docs/specs/<slug>.md]` — for context.
- `docs/usdl/{behavior,topology,interfaces,composition}.xml` — to check the dev's USDL amendments landed in the right place.
- `CLAUDE.md` at the repo root — to confirm the dev did not violate the project's coding conventions (audit vocabulary, comment policy, settings-as-single-source).
- `git diff` over the entire working tree — to see what the dev actually changed.
- `git status --porcelain` — to see new and modified files.

## The contract

You PASS this task only when every single numbered check in the `<VERIFICATION>` block is demonstrably satisfied by the actual files on disk. You do not infer satisfaction from prose, plausibility, or the dev's summary. You verify mechanically:

- If a check says "exits 0", you run the command and check `$?`.
- If a check says "exists", you `test -f` it.
- If a check says "is byte-identical", you `md5sum` or `diff` and confirm.
- If a check says "git diff shows no changes", you run `git diff` and grep for empties.
- If a check involves a regex or grep pattern, you run grep and confirm the match (or absence).
- If a check says a file must contain a specific id or constraint, you `grep -F` and confirm the count.
- If a check requires running a real PGMQ / docker-compose round-trip, you `docker compose up -d` the required services (or assume they are already up), run the test, and confirm the outcome. If the stack is not bringable up in this environment, mark the check as `infra-skipped` in diagnostics with the reason — do not silently pass.

When a check is ambiguous, FAIL it — and explain in the diagnostics what's ambiguous. Better to surface ambiguity than to wave it through.

## Git verification (in addition to the task's own checks)

Independently of the task's `<VERIFICATION>` block, perform these git-level checks:

1. **The dev actually wrote something.** `git status --porcelain` is non-empty. If empty, the dev did nothing — automatic fail.
2. **Only expected paths changed.** Cross-reference `git status --porcelain` against the `## Deliverables` block. Every modified or new file must be either (a) listed in `## Deliverables`, or (b) a justifiable side-effect (a test fixture the task implies, a `.env.example` update the task names). Unexpected files in `git status` are NOT automatic failures but ARE diagnostics — surface them.
3. **No commits.** `git log -1 --pretty=%H` is the same SHA as before the dev ran (the orchestrator's pre-run snapshot). The dev must NOT have committed.
4. **No accidental deletions.** `git status` should not contain `D ` lines for files outside `## Deliverables`. If the task removes files (rare), they must be named in Deliverables.
5. **`tests.md` / `tasks.md` untouched by dev.** `git diff docs/tasks/tasks.md` is empty. The orchestrator owns the Status column; the dev must not touch it.
6. **Branch hygiene.** `git branch --show-current` matches the branch the orchestrator was on at start (do not switch branches mid-task). On `main`, surface this as a diagnostic — the user may not want code landing directly on `main`.

## Output

When done, output ONLY this structured verdict as your final message:

```
VERDICT
verdict: pass | fail
attempts_required: 1   # the verifier doesn't know about retries; the orchestrator does. Leave as 1.
checks_evaluated: N    # how many <VERIFICATION> checks you ran
checks_passed: M       # how many returned satisfaction
checks_skipped_infra: K  # checks that could not run due to missing infra (e.g. docker compose unavailable)
git_state:
  files_modified: [list of paths]
  files_new: [list of paths]
  files_deleted: [list of paths]
  unexpected_paths: [list of paths NOT in ## Deliverables but present in git status]
  tasks_md_unchanged: true | false
  current_branch: <branch name>
  commits_made: 0  # MUST be 0
diagnostics:           # required when verdict=fail; may be empty when verdict=pass
  - check_id: "check-3"        # the verification check that failed (use the numbered prefix)
    reason: "what you observed"
    evidence: "the command output, grep result, or diff that proves the failure"
    mismatch_class: code | spec | test | infra | git
```

If `checks_evaluated + checks_skipped_infra < checks_in_<VERIFICATION>_block`, that itself is a failure: you didn't run them all and didn't account for skips.

## Discipline

- Run every check. Don't skim.
- Don't pass on plausibility — verify mechanically.
- A check that requires running a script must be RUN. If the script doesn't exist yet (the dev forgot to create it), that's a fail, not a skip.
- A check that requires a smoke test (e.g. `docker compose up`, the integration tests under `tests/integration/`) must actually run if the env is available. If it cannot run, file as `infra-skipped`, not `pass`.
- Be terse. The orchestrator needs the VERDICT block; everything else is noise.
```

## Retry policy

When `verdict: fail`, the orchestrator:

1. Takes `diagnostics` verbatim from the verifier's output.
2. Re-spawns the dev agent with the same prompt as before, but with the `## Diagnostics from previous attempt` section populated.
3. On the 4th attempt (after 3 failed retries), escalates: prints the task id, the 3 verifier reports, and STOPS the loop. Does not advance to the next task.

The retry counter is per-task; it resets when moving to the next task.

A verifier verdict of `pass` with `checks_skipped_infra > 0` is **conditional pass**: the orchestrator updates Status to `passed-infra-skipped` (not `passed`) and continues. Tasks whose infra-skipped checks are critical (e.g. T01 integration tests against real PGMQ) should not advance past this gate without an operator re-run when the infra is back.

## Updating tasks.md

After a task passes, the orchestrator updates `docs/tasks/tasks.md`:
- Change the task's `Status` column from `pending` to `passed` (or `passed-infra-skipped` per above).
- Do not commit. The user controls commits.

The orchestrator is the ONLY writer to `tasks.md`. Neither the dev nor
the verifier should touch it.

## Final report

When the loop ends (either drained the queue or escalated), print a
final report:

```
RUN REPORT
processed: 2
passed: 1
passed-infra-skipped: 0
escalated: 1
escalated_task: T02
escalated_reason: "verifier diagnostics on attempts 1,2,3 below"
  attempt 1: <reason>
  attempt 2: <reason>
  attempt 3: <reason>
remaining_pending: T02
notes:
  - "T01 passed cleanly; ran 247 unit tests + 12 integration tests + smoke."
  - "T02 evaluator path failed deny-set-precedence check across all 3 attempts; surfaced ambiguity in spec resolution — recommend PM resolution before re-attempt."
```

## Safety rails

- **Never commit.** Neither dev, verifier, nor orchestrator commits to git. The user controls commits — only after they review the run.
- **Never push to remote.** No `git push`, no PR creation, no `gh pr create`.
- **Never modify `tasks.md` outside the Status column.** The orchestrator updates Status only; the task table structure and dependency columns are preserved.
- **Never delete files.** If the dev wrote unexpected files, the verifier surfaces them as diagnostics; the user decides whether to remove.
- **Never skip dependencies.** A pending task whose predecessors are not `passed` is skipped with a warning; it is never attempted.
- **Never attempt the closed `fix-runner-inbox/` directory.** It is not in the task table; it is the closed multi-file predecessor. The orchestrator's pending-task parser must read only rows in the `## Tasks` table of `docs/tasks/tasks.md`.
- **Spawn one subagent at a time.** Dev runs, then verifier runs. Never both in parallel on the same task — the verifier needs `git diff` of the dev's output.
- **Honour the GSD workflow caveat.** The kloc-agent CLAUDE.md says direct repo edits should normally start through a GSD command. This orchestration is the operator's explicit override; the orchestrator IS the GSD workflow for this run, so the dev subagent may make direct edits. If a future GSD-aware policy lands, update this note.
- **Cross-repo deliverables are out of scope.** T02 references a `kloc-intelligence` endpoint. The dev agent must NOT clone, edit, or push to `kloc-intelligence`. The fixture stat server in this repo is the contract surface; the real endpoint lands as a sibling PR tracked separately.
- **Single-worker invariant.** Tasks must not introduce code paths that assume multi-worker uvicorn. The verifier surfaces any new `os.environ` reads outside `src/settings.py` as a fail (per `con.settings-no-runtime-env-reads`).

## Interruption

The user can interrupt at any point. The orchestrator should be
designed to be re-runnable: re-invoking this prompt picks up wherever
the queue is (pending tasks remaining, paused after the last successful
pass or escalation). The Status column in `docs/tasks/tasks.md` is the
durable state — `passed` rows are skipped on re-run; `pending` rows are
candidates; `passed-infra-skipped` rows can be re-attempted with the
infra available.

## Environment hints (orchestrator MAY share with subagents)

- **Python 3.12 + uv 0.5.4.** Dev runs `uv sync --frozen` once if `uv.lock` has drifted; otherwise the existing venv is used. `pytest` is run as `pytest tests/ -q` (not `python -m pytest`); the project's `pyproject.toml` configures `asyncio_mode = "auto"`.
- **Docker required for integration tests.** `docker compose up -d postgres minio` brings up the data plane; the runner image (`kloc-agent-runner`) is built lazily by the backend at spawn time.
- **PGMQ extension** ships with the `quay.io/tembo/pg16-pgmq:latest` image (the predecessor task did the swap). Verifier checks for T01 that require `pgmq.send` / `pgmq.read` round-trips depend on this image being live.
- **MinIO bucket** named by `ARTIFACT_BUCKET` (default `kloc-artifacts`) must exist before T01's artifact-offload integration test; the lifespan boot creates it.
- **kloc-intelligence MCP server** is operator-managed out of band. For T02 the fixture stat server replaces it; for end-to-end smokes the operator's compose stack must be running on `host.docker.internal:8765`.
