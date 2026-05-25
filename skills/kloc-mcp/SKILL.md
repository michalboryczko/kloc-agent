---
name: kloc-mcp
description: Use kloc-intelligence MCP tools (semantic search + structural graph + Symfony flow awareness) to answer codebase questions efficiently. Use INSTEAD OF grep+file_read when investigating PHP/Symfony PayPo codebases (kyc, order-api, etc.) for questions about symbols, callsites, flows, or behavior. Triggers on any retrieval step where the answer requires recall (find all X) or structural understanding (what triggers what), especially when accuracy matters more than speed.
---

# kloc-intelligence MCP

## Indexed scope

The MCP daemon is connected to a single graph that currently holds the **kyc** project (PayPo Symfony 7 service, `App\` namespace + vendor packages). Class/Method nodes outside `App\` exist in the graph but are NOT enriched (no `explanation` field, not embedded for `kloc_search`). Symfony-flow entries (`:Flow`) are filtered to `App\` only — `kloc_flows` lists 84 entries: ~13 cli, ~25 event, ~14 http, ~32 message.

## Why use this over generic file reads

| Need | file_read only | kloc MCP |
|---|---|---|
| Find all callsites of a symbol | unreliable recall | `kloc_usages` — deterministic |
| Trace a HTTP→event→ESB flow | LLM has to chain manually | `kloc_flows` with triggers in/out |
| "What does this class do?" | read source, summarize | `kloc_explain` — pre-generated NL description |
| Find a concept by natural language | guess keywords and scan | `kloc_search` — semantic embedding |
| Detect dead enum values | needs comparing usages | `kloc_usages` returns zero for unused values |
| Polymorphism (interface impls) | manual class hierarchy walk | `kloc_context --include_impl`, `kloc_overrides`, `kloc_inherit` |

## Bootstrap

The kloc tools (`kloc_resolve`, `kloc_search`, `kloc_source`, `kloc_explain`, `kloc_usages`, `kloc_deps`, `kloc_context`, `kloc_flows`, `kloc_owners`, `kloc_chunks`, `kloc_overrides`, `kloc_inherit`, …) are already exposed to the runner via the kloc-intelligence MCP server. Call them directly — no separate schema-loading step is needed.

## Tool selection decision tree

```
Question is about…

A symbol's existence/location?
  → kloc_resolve (cheap, deterministic)

A concept I can't name precisely?
  → kloc_search (collection=both for first try)
  → If top hit score > 0.7 → reliable
  → If top hits all < 0.5 → concept probably absent OR query needs rewording

"Where is X used?" / "Who calls X?"
  → kloc_usages depth=1 (default)
  → If you want to walk further → depth=2 (but bounds the output)

"What does X depend on?" / "What does X call?"
  → kloc_deps depth=1
  → Don't go past depth=2 — explosion

"Help me UNDERSTAND class/interface X" (default for understanding a class)
  → kloc_context depth=1   ⭐ DEFAULT TOOL for "tell me about X as a unit"
  → Returns: usages + deps + FULL DEFINITION (properties with types,
    method signatures, implements list, constructorDeps) + rich edge
    metadata (refType, callee, on, args) you can't get from usages/deps alone.
  → include_impl=true ALWAYS for interfaces/abstract classes (cross-links
    implementations into both directions).
  → Prefer this over (usages + deps + source) — one call replaces three.

"How does flow F work end-to-end" (HTTP/message/event/CLI)?
  → kloc_flows (no args) to list, then
  → kloc_flows flow_id=<exact> for triggers in/out

"What does this class/method DO" (plain English)?
  → kloc_explain (pre-generated, cheaper than reading source+summarizing)

"Show me the actual code"?
  → kloc_source — addressed BY SYMBOL/FQN (returns content + file + line
    range + char count + token estimate). USE THIS over `file_read` whenever
    you have a symbol — no need to figure out line ranges, and you get
    only the node, not the whole file.
  → kloc_chunks for large classes (method-boundary chunks, embedder-friendly,
    each chunk re-emits the class header for context). USE WHEN a class
    is big enough that `kloc_source` would dump too much at once.
  → file_read (path-based) ONLY when:
    - You need the whole file (configs, YAML, XML)
    - You don't have an FQN yet and `kloc_resolve` is unhelpful
    - You need docblocks AND chunks (chunks strip them; source keeps them)

"Show me the polymorphic implementations"?
  → kloc_overrides (for methods)
  → kloc_inherit (for class hierarchy)
```

## Per-tool guidance

### kloc_search

- Backing collections: `code_embeddings` (raw source), `explain_embeddings` (LLM-generated class/method descriptions), `flow_explain_embeddings` (Symfony flow summaries). `collection: both` merges all three; flow summaries often dominate top-N because their language is concise and business-friendly.
- **WARNING — `collection: code` results are token-heavy**: a single hit can be ~30 KB of test source. Prefer `explain` or `both` unless you specifically need raw code. If you must use `code`, use `limit=3`.
- **Tests inflate `code_embeddings` scores by ~0.05** — discount when comparing.
- Single-word queries (`"weryfikacja"`) return adjacent-but-wrong top hits. Always add a qualifier ("powody odrzucenia weryfikacji").
- Polish queries work but score ~0.025 lower than equivalent English. Multilingual model.
- **Empirical score thresholds (calibrated, not guess)**:
  - `≥ 0.78` strong, almost certainly the right answer.
  - `0.70 – 0.78` solid, worth checking.
  - `0.63 – 0.70` **adjacent — usually NOT the answer** (related concepts that look right but aren't).
  - `< 0.60` noise.
- **Gap-to-#2 is more diagnostic than absolute score.** A 0.74 top hit with #2 at 0.72 is weaker signal than a 0.71 top hit with #2 at 0.61.
- Verbose business-language queries score ~0.03 lower than terse, but find the same nodes. No real penalty for natural language.

### kloc_resolve

- **CRITICAL FOOTGUN**: `kloc_resolve` does name-only **first-match-wins with NO candidates list**. There is no "did you mean…" UX. If you pass `"process"` you'll get back `$process` (a property) or `ProcessStrategyDirector::process()`, not what you asked for.
- `Class::method` syntax **does NOT anchor to the class**. `AbstractProcessStrategy::process` may return `ProcessStrategyDirector::process` silently. Pass full FQNs.
- **Always verify `root.fqn` in the response matches what you wanted** before using the returned node id elsewhere.
- Accepts FQN, partial names, method syntax. Fast — call freely. But prefer full FQN.
- This footgun cascades into `kloc_overrides`, `kloc_chunks`, `kloc_explain` since they share the resolver.

### kloc_flows

- Symfony-aware: HTTP routes, Messenger commands/queries, EventSubscribers, CLI commands.
- Without args → full list (current `kyc`: 84 flows — 13 cli, 25 event, 14 http, 32 message). Each entry has `flow_id`, `type`, `name`, `entry_fqn`, type-specific fields (`route`+`http_methods`, `message_class`, `event_name`, `command_name`).
- `type=http,event` (comma-separated) works as multi-filter (OR).
- `flow_id` resolution: exact id, partial substring (returns candidates list), or entry FQN.
- Detail mode adds: `explanation` (1-3 sentence LLM summary), `entry` (file + line range — but range collapses to declaration line only), `triggers_in`, `triggers_out`.
- **🚨 CURRENT STATE**: `triggers_in` and `triggers_out` are **empty** in the current index (FLOW_TRIGGERS edges not populated). End-to-end flow reconstruction (Q10-style: HTTP → command → event → ESB) **does NOT work via `kloc_flows` alone** today. You must fall back to `kloc_usages` and `kloc_context` to follow dispatch calls between flows.
- Where `kloc_flows` still shines:
  - **Inventory questions**: "is there a webhook from X?", "what HTTP endpoints exist?" — flow list is the answer.
  - **Adversarial check**: "do we have endpoint for Y?" → if no flow has matching name/route → strong "no".
  - **Asymmetry detection** (Q6-style): if `EmitMessageOnEsbEventSubscriber::onXRemoved` is in the list but `onXCreated` isn't → asymmetry instantly visible.
  - **Webhook contracts** (Q8): detail mode gives route + entry file, then read payload class from there.

### kloc_explain

- Returns a 2-5 sentence LLM description of what a class/method does. ~5s latency per call.
- **Classes and Methods ONLY.** On Enum, EnumCase, Property returns `{"error": "Explain is only for Class/Method, got: ..."}`. For enums use `kloc_source` directly.
- **🚨 SILENT PARTIAL-MATCH COERCION — major footgun.** If you pass a symbol that doesn't exist (e.g., `AbstractProcessStrategy::process` when the method is on a different class), `kloc_explain` returns an explanation of the *closest match* with no warning. You will get plausible-sounding output for the wrong class. **Always pre-resolve the symbol with `kloc_resolve` and verify the FQN before calling `kloc_explain`.**
- Prefer over reading source when synthesizing behavior, but for high-risk facts (field names, enum values, payload structure) always confirm with `kloc_source`.
- `force=true` regenerates — only useful if code changed since indexing.

### kloc_source — ⭐ default for "show me the code"

**Use this INSTEAD of `file_read` whenever you have a symbol/FQN.** Returns content + file + line range + char count + token estimate, scoped to the node (class, method, enum) — not the whole file.

**USE WHEN:**
- You have an FQN from `kloc_resolve` or `kloc_search` and want to see the code
- You need to confirm exact field names / payload structure / enum values (high-risk facts)
- You want to know token cost before reading (returned in `tokens_estimate`)
- You want to avoid reading hundreds of lines of unrelated code in the same file

**Keeps docblocks** (unlike `kloc_chunks`, which strips them). Good for "is this method documented?" questions.

**Prefer `file_read` over `kloc_source` only when:**
- You need a whole file (YAML, XML, config) — not a single symbol
- You don't have an FQN and resolve is failing
- You're reading something kloc doesn't index (markdown, scripts, etc.)

### kloc_chunks

- Returns the same chunks used for embeddings. Methods are always **one chunk** with the class header re-emitted as prefix; large classes split by method boundary when they exceed `max_tokens` (default 8000).
- **Strips docblocks** — embedder-friendly, not human-friendly for "is this commented" questions.

**USE WHEN:**
- A class is big (many methods, hundreds of lines) and `kloc_source` would return too much
- You want to scan method-by-method, e.g., to find which method does X without reading the whole class
- You're inspecting the same chunks the embedder saw (debugging `kloc_search` hits)

Skip in favor of `kloc_source` for small classes / single methods.

### kloc_context — ⭐ the default tool for "understand X"

**This is the most powerful single call. Reach for it FIRST when you need to understand a class/interface, not last.**

Returns in one shot:
- **`usedBy`** — bidirectional usages (= what you'd get from `kloc_usages`)
- **`uses`** — bidirectional deps (= what you'd get from `kloc_deps`)
- **`definition`** — full structural block: properties (visibility, types, promoted flags), method signatures, `implements` list, `constructorDeps`
- **Edge metadata** that `usages`/`deps` don't carry: `refType`, `callee`, `on`, `onKind`, constructor `args` map — i.e. *how* something is used, not just *that* it is

**USE WHEN any of these is true** (this list is your trigger — re-read it before every retrieval step):
- You need to understand a class as a whole, not a specific aspect
- The question mentions an interface or abstract class (always use `include_impl=true`)
- You're about to call `kloc_usages` AND `kloc_deps` on the same symbol — collapse into one `kloc_context`
- You need constructor dependencies (DI graph)
- You need to see HOW a method is called (with what args), not just where
- The question is "behavior" type and you've already resolved the entry symbol

**Skip `kloc_context` and reach for the narrower tool when:**
- You only care about callsite recall of an enum value → `kloc_usages`
- You only care about source text → `kloc_source`
- You only care about a single hop direction at depth ≥ 2 → `kloc_usages depth=2` or `kloc_deps depth=2`

**Parameter tips:**
- `depth=1` is enough for most "understand X" cases. `depth=2` only if the immediate neighbors aren't telling you what you need.
- `include_impl=true` is FREE quality — always set it for interfaces/abstracts. For concrete classes it's a no-op.
- For hub classes (e.g., a base entity, `AbstractProcessStrategy`), bump `limit` to 200+ — default 50 silently truncates.

### kloc_deps, kloc_inherit, kloc_overrides

- `kloc_deps` — dependencies only (one direction). **Silently hits `limit=50`** — for hub classes bump to 200. depth=1 ≈ 47 deps on a busy class; depth=2 doubles output; depth=3 reaches shared infra (~30 KB) and noise dominates. Prefer `kloc_context` unless you specifically want one direction.
- `kloc_inherit` — class hierarchy upward (extends/implements). Traits are NOT included.
- `kloc_overrides` — method-level redefinitions downward. Inherits the resolver footgun — pass full FQN.

### kloc_usages

- Killer feature for recall-sensitive questions (rejection reasons, callsites of an enum, places that emit an event).
- `depth=1` is enough for "where is X used". `depth=2` only when you want users-of-users.
- **`limit` default 50** — for popular symbols (base entities, base ProcessStrategy) bump to 200+ or expect silent truncation.
- Includes test files — useful for verifying coverage gaps (zero test usage = suspicious).

## Anti-patterns

- **Calling `file_read` when you have an FQN.** Use `kloc_source` — scoped, with metadata, no need to guess line range.
- **Calling `kloc_source` to read a whole file.** Use `file_read` for that. `kloc_source` is for symbol-scoped reads.
- **Making 2-3 narrow calls on the same symbol** (`kloc_usages` + `kloc_deps` + `kloc_source`). Use `kloc_context` instead — one call, more metadata.
- **Using `kloc_search` to find a known symbol.** Use `kloc_resolve` — deterministic and cheap.
- **Going to depth=3+ on `kloc_deps`.** Output explodes; useless signal.
- **Ignoring scores.** If top 5 hits are all < 0.5 the answer is probably "not in the codebase" — don't pick the highest of bad options.
- **Skipping `domain_translation`.** Calling `kloc_search` with the user's raw biz term often fails. Map "Rumunia" → "RO config" first.
- **Calling `kloc_chunks` on small classes.** Adds no value over `kloc_source`. Use chunks only for large classes.

## Indexing caveats

- The index is rebuilt periodically; if you suspect stale data, flag it to the user.
- Vendored code (`vendor/`) is indexed by default — useful for ESB contracts but be careful with score interpretation when query matches vendor noise.
- Newly added files may take a refresh cycle to appear.
- Currently `triggers_in`/`triggers_out` on flows are not populated — see `kloc_flows` section.

## Known indexer issues to be aware of

- 2 of 13 CLI commands have empty `entry_method_node_id` (AST resolution failure for CLI entries).
- Some `kloc_explain` outputs are truncated mid-sentence — re-run with `force=true` if it matters.
- `kloc_flows` `entry.start_line` / `end_line` collapse to the declaration line — they don't give the method body range.
