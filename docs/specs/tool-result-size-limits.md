# Feature Spec: tool-result-size-limits

## Problem

The runner-side agent has unconstrained access to MCP tools served by `kloc-intelligence`. Two of those tools — `file_read` (reads an arbitrary source file) and `kloc_flows` (returns aggregated flow data for the indexed codebase) — can legitimately return many MiB of data inline. When the agent invokes `file_read` against a large generated file (vendor blob, compiled artifact, minified asset, fixture dump) or `kloc_flows` without a bounded `depth` / `limit`, the entire result is emitted as a single `ToolCallResult` AG-UI event.

Today this is harmful in three independent ways:

1. **Memory + context budget.** The tool result lives in runner memory, then in the model's context window. A 5 MiB JSON blob eats the next turn's input budget for no analyst value — the agent typically responds by summarising the first N lines anyway.
2. **Channel poisoning.** Until `fix-runner-communication.md` lands, an oversized tool result deterministically poisons the runner ↔ backend JSONL channel (1 MiB line cap → 413 → infinite replay). Even after that fix raises the cap to 16 MiB and adds a runner-side offload path, the agent has *no incentive* to avoid the call — the offload is silent and the agent does not learn from the policy decision.
3. **No way to deny.** The platform's only existing policy lever is `KLOC_DENY_TOOLS`, a binary allow/deny by tool name. There is no per-argument inspection, so an operator cannot say *"`file_read` is fine in general, but refuse it against files > 256 KiB"*. The hook seam exists (`BeforeToolCall` carries `tool_name` + `args` to `src/hooks_audit/policy.py`) but the policy implementation is one if-statement over a set.

The result: the agent reaches for `file_read` indiscriminately because nothing in its observation loop punishes the choice, and the platform absorbs the cost.

## Decision

**Per-tool argument-aware policy enforced at `cmp.backend.hooks-audit.policy.decide`, with actionable hints fed back to the agent via the existing `ToolCallDenied` AG-UI event.**

The existing `BeforeToolCall` webhook flow stays intact end-to-end. The change is inside `Policy.decide`: it consults a new `KLOC_TOOL_LIMITS` setting (JSON-typed) that names per-tool predicates. For each known tool, a `ToolPolicyEvaluator` implementation receives `args` and returns either `None` (no opinion → allow) or `{"decision": "deny", "reason": "tool_limit:<kind>", "hint": "<actionable message>"}`. The runner already wires `cancel_tool = reason` and emits a `ToolCallDenied` AG-UI event — the AG-UI event is extended with one optional `hint: str` field so the agent reads the denial reason as a tool result and can re-plan inline.

For `file_read`, the evaluator inspects the `path` argument and consults a small backend-side stat helper that hits a new `kloc-intelligence` endpoint (`/v1/file_stat?path=...`). Cap exceeded → deny with a hint that suggests `start_line` / `end_line` or `kloc_flows` summaries. For `kloc_flows`, the evaluator inspects `depth` / `limit` arguments and refuses unbounded calls. Stat-call failure (timeout, kloc-intelligence unreachable) returns `allow` — the cap is a best-effort guidance layer, not a security boundary. The hard safety net stays the channel-level cap from `fix-runner-communication.md`.

This is a per-operator policy, not per-Session. Defaults ship in `.env.example`.

## Acceptance Criteria

### Functional — denial path

**AC1.**
```gherkin
Scenario: file_read against an oversize file is denied with an actionable hint
  Given KLOC_TOOL_LIMITS sets file_read.max_bytes = 262144 (256 KiB)
    And the indexed codebase contains /workspace/vendor/big.json of 5_242_880 bytes
  When the agent calls file_read(path="/workspace/vendor/big.json")
    And the runner emits the BeforeToolCall webhook
  Then policy.decide returns
       {"decision": "deny",
        "reason": "tool_limit:file_too_large",
        "hint": "file is 5.0 MiB (cap 256 KiB); re-call with start_line/end_line, or use kloc_flows for a summary"}
    And the runner sets event.cancel_tool = "tool_limit:file_too_large"
    And the FE receives a ToolCallDenied AG-UI event carrying the hint
    And audit_log gets a tool_call.denied row with reason="tool_limit:file_too_large"
    And the agent's next turn does NOT retry the same tool call with identical args
```

**AC2.**
```gherkin
Scenario: file_read with a byte-range under the cap is allowed
  Given KLOC_TOOL_LIMITS sets file_read.max_bytes = 262144
  When the agent calls file_read(path="/workspace/big.json", start_line=1, end_line=100)
    And the requested line range serialises to under 256 KiB
  Then policy.decide returns {"decision": "allow"}
    And the tool result is delivered to the agent normally
```

**AC3.**
```gherkin
Scenario: kloc_flows without a depth or limit argument is denied
  Given KLOC_TOOL_LIMITS sets kloc_flows.require_bounded = true
  When the agent calls kloc_flows() with no depth and no limit
  Then policy.decide returns
       {"decision": "deny",
        "reason": "tool_limit:unbounded",
        "hint": "kloc_flows requires depth or limit; try kloc_flows(depth=2) for an overview"}
```

**AC4.**
```gherkin
Scenario: stat-call failure does not block legitimate tool calls
  Given KLOC_TOOL_LIMITS sets file_read.max_bytes = 262144
    And the kloc-intelligence /v1/file_stat endpoint is unreachable
  When the agent calls file_read(path="/workspace/small.php")
    And the stat call from policy.decide times out after 500 ms
  Then policy.decide returns {"decision": "allow"}
    And a warning is logged
    And an OTel counter kloc_agent.policy.stat_unavailable_total is incremented
```

### Configuration

**AC5.** A new setting `KLOC_TOOL_LIMITS` is parsed as JSON into a typed `ToolLimitsConfig` Pydantic model on `Settings`. Empty or unset → no limits enforced (allow). Example:
```json
{
  "file_read": {"max_bytes": 262144},
  "kloc_flows": {"require_bounded": true, "max_results": 200}
}
```
Tools not named in the document have no cap. Malformed JSON fails fast at boot.

**AC6.** A new setting `KLOC_INTELLIGENCE_STAT_URL` names the file-stat endpoint the backend calls during `Policy.decide`. Default derived from `KLOC_MCP_URL` by replacing the path with `/v1/file_stat`.

**AC7.** `.env.example` documents both settings with conservative defaults (`file_read.max_bytes = 262144`, `kloc_flows.require_bounded = true, max_results = 200`).

### Code structure

**AC8.** `src/hooks_audit/policy.py:Policy.decide` is rewritten to:
1. Short-circuit non-`BeforeToolCall` events to `allow` (unchanged).
2. If `tool_name` is in `KLOC_DENY_TOOLS`, deny with `reason="test-deny:<tool>"` (unchanged, lowest precedence).
3. Look up a `ToolPolicyEvaluator` by tool name; if present, `evaluator.evaluate(args)` returns `PolicyDecision | None`. None → allow.

**AC9.** A new module `src/hooks_audit/evaluators/`:
- `__init__.py` exposes `EVALUATORS: dict[str, ToolPolicyEvaluator]`.
- `file_read.py` — `FileReadEvaluator`. Reads `path`, optional `start_line` / `end_line` / `max_bytes` args, calls `stat_client.stat(path)` for byte size, compares against cap.
- `kloc_flows.py` — `KlocFlowsEvaluator`. Reads `depth` / `limit` args; denies if both absent when `require_bounded=true`.
- Evaluators implement `Protocol`: `def evaluate(self, args: dict) -> PolicyDecision | None`.

**AC10.** A new module `src/hooks_audit/stat_client.py`: thin async httpx wrapper. `timeout=500 ms`, single-attempt. On any exception logs a warning, increments the OTel counter from AC4, returns `None`.

**AC11.** `runner/hooks/audit.py:_emit_tool_call_denied` is extended with an optional `hint: str` parameter wired into the `ToolCallDenied` CUSTOM event's `value` dict. Existing call sites continue to work without the field.

### kloc-intelligence (cross-repo dependency)

**AC12.** `kloc-intelligence` exposes a private HTTP endpoint `GET /v1/file_stat?path=<path>` returning `{"exists": bool, "size_bytes": int, "is_file": bool}`. JSON only, no MCP wrapping. The endpoint is mounted in the existing `mcp-server-http` ASGI app on a separate path so kloc-agent's policy layer can reach it without going through MCP.

**AC13.** **PM decision:** does `kloc-intelligence` also expose `file_stat` as an MCP *tool* the agent can call directly? Recommendation: yes — agents that know about it can pre-flight a stat instead of trying to read and getting denied. Spec ships with both; cross-repo task tracks the kloc-intelligence side.

### Tests

**AC14.** `tests/hooks_audit/test_policy_tool_limits.py`:
- `file_read` under cap → allow.
- `file_read` over cap → deny with hint matching AC1 prose.
- `file_read` with `start_line` / `end_line` reducing the byte range → allow.
- `file_read` against missing path → allow with debug log (let kloc-intelligence return the error).
- `file_read` with stat-call timeout → allow + counter incremented.
- `kloc_flows` unbounded → deny.
- `kloc_flows(depth=2)` → allow.
- `kloc_flows(limit=50)` → allow.
- Unknown tool → allow.
- Tool in both `KLOC_DENY_TOOLS` and `KLOC_TOOL_LIMITS` → `KLOC_DENY_TOOLS` precedence (deny with `test-deny:<tool>`, not `tool_limit:*`).
- Malformed `KLOC_TOOL_LIMITS` JSON → boot raises.

**AC15.** `tests/integration/test_file_read_denial.py`: real runner against a real backend with a fixture kloc-intelligence stub. Agent calls `file_read` against a 5 MiB fixture, FE observes `ToolCallDenied` with the hint string, agent's next turn does not retry the same call. Marked `@pytest.mark.integration`.

**AC16.** `tests/unit/test_stat_client.py`: timeout returns None + counter incremented; happy path returns size; 404 returns `{exists: false}`.

**AC17.** Existing suite (`pytest tests/ -q`) remains green.

### Audit + observability

**AC18.** No new audit event names. The existing `tool_call.denied` carries the new reasons (`tool_limit:file_too_large`, `tool_limit:unbounded`, ...). `AuditEventType` is unchanged. Downstream analysis distinguishes via the `reason` field in the payload.

**AC19.** New OTel signals:
- Counter `kloc_agent.policy.deny_total{tool=...,reason=...}` — one per deny.
- Counter `kloc_agent.policy.stat_unavailable_total` (AC4).
- Histogram `kloc_agent.policy.stat_latency_ms` — stat-call round-trip.

### Documentation

**AC20.** `docs/usdl/composition.xml`:
- `cmp.backend.hooks-audit` structure description names `Policy`, `EVALUATORS`, `stat_client.py`.
- New constraint `con.tool-policy-argument-aware`: *"`Policy.decide` consults a per-tool `ToolPolicyEvaluator` registry when `event_type == BeforeToolCall`; the evaluator inspects `args` and may deny with a reason and a hint."*
- New constraint `con.policy-stat-best-effort`: *"Stat-call failure returns `allow`; the cap is guidance, not a security boundary."*

**AC21.** `docs/usdl/interfaces.xml`:
- New contract `ifc.intel-stat` documents the `/v1/file_stat` endpoint, provider `top.kloc-intelligence`, consumer `top.backend`.
- The existing `BeforeToolCall` webhook contract is extended to document the `hint` field on the deny response.

**AC22.** `docs/usdl/topology.xml`:
- New communication edge `top.com.backend-to-intel-stat` carrying `ifc.intel-stat`.

**AC23.** `.env.example` documents `KLOC_TOOL_LIMITS` and `KLOC_INTELLIGENCE_STAT_URL` with the recommended defaults from AC7 plus inline comments explaining the cap rationale.

## Non-Goals

- Streaming partial file reads. The `start_line` / `end_line` arguments already exist on `kloc-intelligence`'s `file_read`; this spec doesn't extend the tool surface.
- AfterToolCall truncation. Silently shortening a result hides data from the agent — the deny + hint path is preferred because the agent learns and re-plans.
- Per-Session policy overrides. `KLOC_TOOL_LIMITS` is per-operator (env-driven) only.
- Number-of-tool-calls-per-turn caps. Separate concern; if needed, a different evaluator can implement it later.
- Multi-tenant policy (different limits per Analyst). Single-operator product per the milestone constraints.
- Caching stat results across `Policy.decide` calls. The stat round-trip is one HTTP hop on the docker bridge — measure first before adding a cache.
- Replacing `KLOC_DENY_TOOLS`. The new evaluator layer is additive; the deny set keeps lower precedence.
- Coordinating with the channel-side cap from `fix-runner-communication.md` beyond the layered relationship documented in the Decision section. The two specs ship independently.

## Open Items (require PM resolution before implementation closes)

1. **`file_stat` MCP exposure.** See AC13. Recommend: yes, expose as both a private HTTP endpoint (backend uses it) and an MCP tool (agent can pre-flight). PM/architect to confirm before kloc-intelligence-side work starts.
2. **Default cap values.** AC7 sets `file_read.max_bytes = 262144` (256 KiB) and `kloc_flows.max_results = 200`. These are guesses for a PoC analyst workflow. PM decision: ship with these defaults active out of the box, or ship with `KLOC_TOOL_LIMITS` empty and let operators opt in? Recommend: ship empty, document the recommended starting values in `.env.example` so the operator's first encounter is intentional opt-in.
3. **Hint copy.** AC1 and AC3 quote the hint strings verbatim. Are these acceptable as user-visible copy (the agent quotes them back to the Analyst) or should the runner translate them through a copy table? Recommend: ship the strings as-is for v1, revisit when localisation lands.
4. **Behavior.xml updates.** This spec introduces a new user-visible behavior — "the agent self-corrects when a tool is denied with a hint". Should it be captured as a `<use-case>` or `<rule>` under `beh.ask-assistant`, or is the AG-UI event change documented at the interface layer sufficient? Recommend: a `<rule>` under `beh.ask-assistant` named *"tool denials carry an actionable hint the agent observes as the tool result"*.
5. **Audit payload schema versioning.** AC18 reuses `tool_call.denied` with a new `reason` namespace (`tool_limit:*`). Downstream analytics (if any) need to know. Confirm no other consumer of `tool_call.denied` will break on unfamiliar `reason` strings. Recommend: document the namespace in the audit-vocabulary docstring in `src/db/models.py`.
