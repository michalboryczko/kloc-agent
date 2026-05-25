# Feature Spec: runner-project-files

Read-only project-source access for the runner, in two coupled parts:

1. A shared, compose-seeded **`kloc-projects` named volume** mounted RO at `/projects` in every runner container.
2. A runner-side **`read_project_file` tool** that the orchestrator uses to fetch a file by `(project_name, path)` — scoped, validated, size-capped.

The two ship together. Either alone is dead weight: the volume is unreadable without a tool the agent can name, and the tool has nothing to read without the volume.

## Problem

`kloc-intelligence` indexes a project's PHP source into Neo4j + Qdrant and serves derived views (graph, semantic chunks, flow summaries) over MCP. It does **not** expose verbatim, on-disk file content for files the indexer didn't pull, or for ranges the chunker collapsed. So today, when an analyst asks "show me the actual code of `App\Order\OrderFactory::create` line 42 onward" or "what's in `config/packages/messenger.yaml`", the orchestrator has three bad options:

1. Call `kloc_source` / `kloc_chunks` — works for indexed PHP classes, but loses files outside the index (YAML, XML, JSON fixtures, Markdown, scripts) and loses byte-exact ranges (the chunker may not align to the line the analyst asked about).
2. Call `file_read` (the `strands_tools.file_read` already registered at `runner/agent_factory.py:230,242`) — works for **arbitrary** paths the runner container has access to. The runner image has no project source mounted, so this currently resolves nothing useful, and if it were given a wide bind it would happily walk the entire host filesystem because nothing scopes it.
3. Give up and say "z kodu wynika X" without the citation file:line discipline the base prompt demands (`src/api/stream.py:424-425`).

The repo already has a working pattern for handing the runner a read-only tree: the `kloc-skills` and `kloc-agents` named volumes, seeded by one-shot init sidecars (`docker-compose.yml:65-95`) and surfaced via `build_skills_mount` / `build_agents_mount` in `src/runner_mgmt/hydrate.py:123-150`. That pattern is the right shape — same mechanism for the same constraint (aiodocker can't bind-mount paths resolved inside the backend container, see the comment block at `hydrate.py:126-130`).

What is missing is:
- A third named volume scoped to project source, with one subtree per project,
- A tool whose argument schema names a project (so the agent self-restricts to the intended subtree), validates path containment, and applies a byte cap so a careless `read_project_file(project_name="kyc", path="var/log/all.log")` against a 200 MiB file does not poison the channel.

This spec adds both.

## Decision

### Volume

Add a compose-managed named volume **`kloc-projects`**, seeded by a one-shot **`projects-init`** sidecar from the repo-root **`./projects/`** directory, mirroring `skills-init` and `agents-init` line-for-line. Layout on the volume:

```
kloc-projects/
├── kyc/           ← project subtree (read-only mirror of the kyc service)
│   ├── src/
│   ├── config/
│   └── …
├── order-api/
│   ├── src/
│   └── …
└── …
```

A new `build_projects_mount()` in `src/runner_mgmt/hydrate.py` returns an aiodocker `Mount` dict targeting `/projects` with `ReadOnly: True`. `src/runner_mgmt/docker_runner.py:144-148` appends it to the runner's `HostConfig.Mounts`. The volume name is overridable via `KLOC_PROJECTS_VOLUME` (defaults to `kloc-projects`), and the runner-side target path is overridable via the new `HydrationPayload.projects_dir: str = "/projects"` field, defaulting to `/projects`.

The volume is **single-tenant per deployment**. Every project on the volume is reachable by every runner spawned by that backend. A future per-session allowlist (`HydrationPayload.allowed_projects: list[str] | None = None`) is left as a deferred hook in the tool implementation — see "Deferred / out of scope" below.

### Tool

Add a runner-side Strands `@tool`-decorated function **`read_project_file`** in `runner/tools/project_files.py`, registered alongside `file_read` in `runner/agent_factory.py:241-243`. Schema:

```python
@tool(
    name="read_project_file",
    description=(
        "Read a single file from one of the mounted project source trees "
        "under /projects/{project_name}/. Use this whenever the user "
        "names a project AND asks about a concrete file path (e.g. "
        "`src/Order/OrderFactory.php`, `config/services.yaml`). "
        "Prefer kloc_source / kloc_chunks when you only need a chunk of "
        "indexed PHP; use this tool for byte-exact ranges, "
        "non-PHP files (YAML/XML/JSON/MD), or files outside the index."
    ),
)
def read_project_file(
    project_name: str,        # e.g. "kyc", "order-api"
    path: str,                # relative to /projects/{project_name}/, no leading slash
    start_line: int | None = None,
    end_line: int | None = None,
) -> str: ...
```

Behaviour:

- `project_name` must match `^[a-z][a-z0-9-]{0,63}$` (same regex as `agents_loader._NAME_RE`). Anything else → return a structured error string the agent can parse: `"error: invalid project_name; must match ^[a-z][a-z0-9-]*$"`.
- The absolute filesystem path is `Path(projects_dir, project_name, path).resolve()`. After resolution it MUST be a strict descendant of `Path(projects_dir, project_name).resolve()` (string-prefix check on the resolved paths). Failing that → `"error: path escapes /projects/{project_name}/"`.
- The project subdirectory must exist on the volume → otherwise `"error: project {project_name} not mounted"`.
- The file must exist and be a regular file (not symlink-to-outside, not directory, not FIFO) → otherwise `"error: not a regular file: {path}"`.
- File size is bounded by `KLOC_TOOL_LIMITS.read_project_file.max_bytes` (default 256 KiB, same as `file_read` per `docs/specs/tool-result-size-limits.md:32`). Oversize file → policy-evaluator path (deny via `tool_limit:file_too_large` hint), so the agent learns to re-call with `start_line`/`end_line`.
- `start_line` / `end_line` are inclusive, 1-indexed, applied AFTER size cap on the full file. If both omitted, return the whole file (subject to the byte cap).
- Output is plain text (the file's UTF-8 decoding). Binary files (best-effort `text/plain` check via the first 8 KiB) → `"error: file appears to be binary; this tool returns text only"`.

The tool is **not** routed through MCP. It is a native Strands tool on the runner, registered at agent build time. Audit hooks (`BeforeToolCallEvent` / `AfterToolCallEvent`, `runner/hooks/audit.py:108,253`) catch it for free: `tool_name="read_project_file"`, `args={"project_name": ..., "path": ..., ...}`. No new audit event names.

### Prompt steering

`src/api/stream.py:400-441` base prompt gains two lines under "Tools available to you":

> - read_project_file — read a single file from a mounted project source tree (`/projects/{project_name}/...`). Use this whenever the user names a project and refers to a concrete file path; prefer it over `file_read` for project source.

…and one line under "Hard rules":

> - When the user names a project and references a file path (`src/...`, `config/...`, `*.yaml`, `*.php`), call `read_project_file(project_name=..., path=...)` for the exact bytes before quoting them.

No skill body changes. `biz-codebase-explorer` and `codebase-qa` are unaffected — they already require file:line citations, and the new tool just makes those citations cheaper to verify.

## Acceptance Criteria

### Functional — volume

**AC1.**
```gherkin
Scenario: kloc-projects volume is seeded from ./projects on first boot
  Given the repo contains ./projects/kyc/src/Order/OrderFactory.php
    And docker-compose.yml declares the kloc-projects named volume
    And the projects-init sidecar is configured to seed kloc-projects from ./projects
  When `docker compose up backend` runs on a fresh environment
  Then the kloc-projects volume contains /kyc/src/Order/OrderFactory.php
   And the projects-init container exits with status 0
   And the projects-init logs include "projects-init done"
```

**AC2.**
```gherkin
Scenario: runner sees /projects mounted read-only
  Given a backend with KLOC_PROJECTS_VOLUME unset (defaults to "kloc-projects")
    And the kloc-projects volume contains /kyc/src/A.php
  When the backend spawns a runner for any session
  Then the runner container's HostConfig.Mounts includes
       {"Type": "volume", "Source": "kloc-projects", "Target": "/projects", "ReadOnly": true}
   And the runner can read /projects/kyc/src/A.php
   And the runner CANNOT write to /projects/* (EROFS)
```

**AC3.**
```gherkin
Scenario: KLOC_PROJECTS_VOLUME env overrides the source volume name
  Given KLOC_PROJECTS_VOLUME=my-other-projects
    And the my-other-projects named volume exists
  When the backend spawns a runner
  Then HostConfig.Mounts uses Source="my-other-projects" with Target="/projects"
```

**AC4.**
```gherkin
Scenario: projects_dir override flows through HydrationPayload
  Given a HydrationPayload with projects_dir="/custom-projects"
  When the runner reads the payload
  Then read_project_file resolves paths under /custom-projects/{project_name}/
   And the docker-compose-managed mount target remains /projects
        (override is consumer-side, not mount-side, for parity with skills_dir/agents_dir)
```

### Functional — tool, happy path

**AC5.**
```gherkin
Scenario: read a small PHP file in full
  Given /projects/kyc/src/Order/OrderFactory.php exists and is 4_321 bytes
  When the agent calls read_project_file(project_name="kyc",
                                          path="src/Order/OrderFactory.php")
  Then the tool result is the UTF-8 decoded body of the file
   And the result is delivered to the agent as a ToolCallResult AG-UI event
   And audit_log gets an AfterToolCall row with tool_name="read_project_file"
```

**AC6.**
```gherkin
Scenario: read a line range
  Given /projects/kyc/config/packages/messenger.yaml is 800 bytes / 30 lines
  When the agent calls read_project_file(project_name="kyc",
                                          path="config/packages/messenger.yaml",
                                          start_line=10, end_line=20)
  Then the tool result contains exactly lines 10..20 inclusive (11 lines)
   And the result does NOT contain lines 1..9 or 21..30
```

### Functional — tool, rejection paths

**AC7.**
```gherkin
Scenario: invalid project_name is rejected synchronously
  When the agent calls read_project_file(project_name="../etc",
                                          path="passwd")
  Then the tool result is exactly
       "error: invalid project_name; must match ^[a-z][a-z0-9-]*$"
   And NO filesystem call is made
   And the AfterToolCall audit row records the rejection in result_preview
```

**AC8.**
```gherkin
Scenario: path traversal outside the project subtree is rejected
  Given /projects/kyc/ exists
    And /projects/order-api/secret.env exists
  When the agent calls read_project_file(project_name="kyc",
                                          path="../order-api/secret.env")
  Then the tool result is
       "error: path escapes /projects/kyc/"
   And the file /projects/order-api/secret.env is NOT opened
```

**AC9.**
```gherkin
Scenario: symlink escaping the project subtree is rejected
  Given /projects/kyc/danger -> /etc/passwd  (symlink installed in the seed)
  When the agent calls read_project_file(project_name="kyc", path="danger")
  Then the tool result is
       "error: path escapes /projects/kyc/"
   And /etc/passwd is NOT read
```

**AC10.**
```gherkin
Scenario: unmounted project is reported
  Given /projects/kyc exists
    And /projects/billing does NOT exist
  When the agent calls read_project_file(project_name="billing",
                                          path="src/whatever.php")
  Then the tool result is "error: project billing not mounted"
```

**AC11.**
```gherkin
Scenario: regular-file check rejects directories and special files
  Given /projects/kyc/src/ is a directory
  When the agent calls read_project_file(project_name="kyc", path="src")
  Then the tool result is "error: not a regular file: src"
```

**AC12.**
```gherkin
Scenario: binary file is refused with a clear hint
  Given /projects/kyc/var/cache/container.cache.php is a serialized binary blob
  When the agent calls read_project_file(project_name="kyc",
                                          path="var/cache/container.cache.php")
  Then the tool result is
       "error: file appears to be binary; this tool returns text only"
```

### Functional — size policy

**AC13.**
```gherkin
Scenario: oversize file is denied by policy with a re-plan hint
  Given KLOC_TOOL_LIMITS sets read_project_file.max_bytes = 262144 (256 KiB)
    And /projects/kyc/vendor/big.json is 5_242_880 bytes
  When the agent calls read_project_file(project_name="kyc",
                                          path="vendor/big.json")
    And the runner emits BeforeToolCall
  Then policy.decide returns
       {"decision": "deny",
        "reason": "tool_limit:file_too_large",
        "hint": "file is 5.0 MiB (cap 256 KiB); re-call with start_line/end_line"}
   And the runner sets event.cancel_tool = "tool_limit:file_too_large"
   And the FE receives a ToolCallDenied AG-UI event carrying the hint
```

**AC14.**
```gherkin
Scenario: oversize file with explicit line range is allowed when the range fits the cap
  Given KLOC_TOOL_LIMITS sets read_project_file.max_bytes = 262144
    And /projects/kyc/vendor/big.json is 5 MiB
  When the agent calls read_project_file(project_name="kyc",
                                          path="vendor/big.json",
                                          start_line=1, end_line=50)
    And the serialised 50-line slice is 8 KiB
  Then policy.decide returns {"decision": "allow"}
   And the tool result is the 50-line slice
```

### Functional — prompt + tool registration

**AC15.**
```gherkin
Scenario: read_project_file is registered on the orchestrator's tool list
  When build_agent runs at runner startup
  Then the Agent's tools list contains a tool named "read_project_file"
   And it appears alongside (not replacing) file_read
   And subagents constructed via build_subagents ALSO receive
       read_project_file in their tool list
       (parity with the file_read wiring at agent_factory.py:230)
```

**AC16.**
```gherkin
Scenario: base_prompt advertises the tool and steers the model
  When the backend builds a HydrationPayload (src/api/stream.py:400-441)
  Then base_prompt contains the substring
       "read_project_file — read a single file from a mounted project"
   And base_prompt contains the substring
       "call `read_project_file(project_name=..., path=...)`"
```

### Configuration

**AC17.** `.env.example` documents:
```bash
# kloc-projects: name of the docker-managed volume holding project sources.
# Seeded from ./projects/ by the projects-init sidecar on first boot.
KLOC_PROJECTS_VOLUME=kloc-projects

# Optional: per-tool cap; oversize reads are denied with a re-plan hint.
KLOC_TOOL_LIMITS={"read_project_file": {"max_bytes": 262144}}
```

**AC18.** Settings parses `KLOC_PROJECTS_VOLUME` as `str` with default `"kloc-projects"`. Empty string → boot error (`Settings._validate_*` style).

### Tests

**AC19.** `tests/runner_mgmt/test_hydrate_projects_mount.py`:
- `build_projects_mount()` returns the expected aiodocker dict shape (`Type=volume`, `Target=/projects`, `ReadOnly=true`).
- `KLOC_PROJECTS_VOLUME` env var overrides `Source`.

**AC20.** `tests/runner_mgmt/test_docker_runner_mounts.py` (or wherever the mount list is asserted today):
- The spawn config's `HostConfig.Mounts` contains exactly four entries: hydration, skills, agents, projects (in that order).

**AC21.** `tests/runner/test_read_project_file.py` covers AC5–AC12 against a `tmp_path`-backed fake `/projects`.

**AC22.** `tests/hooks_audit/test_policy_tool_limits.py` gains AC13/AC14 cases for `read_project_file`.

### Observability

**AC23.** No new audit event types. `BeforeToolCall` / `AfterToolCall` carry `tool_name="read_project_file"` and the rejection reason (if any) via the existing `result_preview` truncation rules (`runner/hooks/audit.py:348-355`). The size-cap denial path reuses `tool_limit:file_too_large` from `docs/specs/tool-result-size-limits.md`.

**AC24.** The OTel meter `kloc_agent.runner.subagents_loaded` is unaffected; no new meter is required. (Optional follow-up — emit `kloc_agent.runner.project_files_read_total{project=...,outcome=...}` counter — left for an operational follow-up if usage volume justifies it.)

## Implementation Notes

### Files touched

- `docker-compose.yml` — add `projects-init` service + `kloc-projects` volume entry, mirroring `skills-init` / `agents-init`.
- `src/runner_mgmt/hydrate.py` — add `build_projects_mount()`; mirror the existing `build_skills_mount` / `build_agents_mount` shape (constants resolved lazily for env-override).
- `src/runner_mgmt/docker_runner.py:144-148` — append `build_projects_mount()` to the `Mounts` list.
- `src/db/models.py:337-344` — add `projects_dir: str = "/projects"` to `HydrationPayload`.
- `src/api/stream.py:400-441,461-474` — extend `base_prompt` per AC16; extend `HydrationPayload(...)` call site to pass `projects_dir`.
- `src/settings.py` — add `KLOC_PROJECTS_VOLUME` (with the same lazy / `.env`-driven shape as `KLOC_SKILLS_VOLUME` / `KLOC_AGENTS_VOLUME`); register `read_project_file` under the existing `KLOC_TOOL_LIMITS` config from `tool-result-size-limits.md`.
- `runner/tools/project_files.py` (new) — `read_project_file` implementation; pure, no I/O beyond `pathlib` + `Path.read_text` / `read_bytes`.
- `runner/agent_factory.py:230,241-243` — import + append `read_project_file` to both `subagent_tools` and orchestrator `tools` (parity with `file_read`).
- `.env.example` — AC17 stanza.

### Filesystem & security

- Path resolution uses `Path.resolve(strict=True)`. The strict flag means a symlink to a nonexistent target raises and is treated as `not a regular file`.
- The containment check is a literal `str(resolved_path).startswith(str(resolved_project_root) + os.sep)`, **not** `parent` walks — this catches the edge case of a symlink resolving to a sibling under `/projects/other-project/`.
- The binary-file heuristic: read the first 8 KiB, attempt `bytes.decode("utf-8")`. On `UnicodeDecodeError`, refuse. This is intentionally conservative; UTF-16-BOM files refuse too, which is fine for the PoC scope (Symfony source is UTF-8). A real `python-magic` dependency is out of scope.
- The tool returns text; the runner's existing JSONL ToolCallResult plumbing handles the framing. The size cap is enforced **before** the slice, so a 5 MiB file with a 50-line slice is allowed iff the file is opened streaming and only the slice bytes are surfaced. Implementation reads the full file via `Path.read_text` only when no slice is requested AND `stat().st_size <= max_bytes`; otherwise reads line-by-line with `io.open(...).readlines()` bounded by `end_line`.

### Symmetry with subagent / skills loaders

The volume side is intentionally a near-clone of `skills-init` / `agents-init`. The docstring in `runner/agents_loader.py:6` already explains *why* the patterns are kept aligned ("operators reason about one loader pattern"); this spec extends that to a third volume. No new abstraction layer is introduced.

## Deferred / out of scope

1. **Per-session project allowlist.** `HydrationPayload.allowed_projects: list[str] | None`. The tool would refuse projects not in the list with `"error: project {x} not authorised for this session"`. Plumbing exists; this spec just declines to use it (single-tenant deploys don't need it). Add when a multi-tenant deployment hits the seam.
2. **Directory listing tool.** A sibling `list_project_files(project_name, path)` would help the agent discover paths it doesn't already know from kloc-intelligence output. Not in this spec because every analyst path so far comes back from `kloc_resolve` / `kloc_search` / `kloc_flows`; the agent rarely guesses paths.
3. **Streaming for large slices.** The current tool returns one string. A 250 KiB slice is fine; a multi-MiB slice would need chunked delivery. The size cap from AC13 prevents this from biting in practice.
4. **Project metadata file.** A `projects/<name>/.kloc-project.yaml` (with display name, language, kloc-intelligence index URL) would let the FE render a project picker and let the agent confirm a project is indexed before reading. Left as a follow-up.
5. **Cross-tool with `file_read`.** `file_read` remains for SKILL.md / config files outside `/projects/` (it's used by skill progressive disclosure in repo 2's pattern, see `runner/agent_factory.py:196-202`). A future cleanup could route all reads through `read_project_file` when the path starts with `/projects/` and reject other paths — that hardening is not required for the PoC.

## Open questions for PM

1. **Default cap.** Mirror `file_read.max_bytes = 262144` (256 KiB)? Or larger, since Symfony YAML/XML configs often run 100–400 KiB? Recommend: ship at 262144 and re-tune from real usage.
2. **Repo-root `./projects/` checked in vs operator-mounted.** Compose seeds `kloc-projects` from `./projects/` on the operator host. Real projects can be many GiB. Option A: keep `./projects/` empty in git and let operators populate it locally before `docker compose up`. Option B: bind `./projects/` from the host instead of using a named-volume mirror, accepting the aiodocker path-resolution constraint (which means we lose runner-from-arbitrary-CWD support — the limitation already documented at `hydrate.py:126-130`). Recommend A.
3. **Symlinks in the seed.** `./projects/<name>/` typically lives outside the kloc-agent repo. Operators will likely symlink: `./projects/kyc -> /home/me/code/paypo-kyc`. The `cp -R` in the init sidecar resolves the symlink and copies the contents — that's correct for "frozen snapshot" semantics but wrong for "live source". If we want live source, the init sidecar must be replaced with a bind-mount (option B above). Recommend: ship with `cp -R` (frozen), and note the re-seed procedure (`docker compose down -v projects-init; docker compose up projects-init`) in the README.
