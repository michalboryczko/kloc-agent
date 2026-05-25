---
name: codebase-qa
description: "End-to-end workflow for answering business-analyst questions about a PayPo PHP/Symfony codebase. Orchestrates question decomposition → kloc-intelligence retrieval → answer synthesis with anti-hallucination guardrails. Use when a non-developer (analyst, PM, QA, stakeholder) asks any natural-language question about how the code works, what it does, what fields/events exist, when a rule applies, etc. Leans on two supporting skills — `decompose` (Layer 1) and `kloc-mcp` (Layer 2 retrieval)."
---

# Codebase Q&A Orchestrator

This skill ties together the three layers of analyst-style codebase Q&A: **decompose → retrieve → synthesize**. It is the entry point. Do not skip steps.

## Indexed project

The kloc-intelligence MCP currently has the **kyc** project indexed (PayPo PHP/Symfony 7, `App\` namespace). All `kloc_*` calls resolve against that graph. If the analyst's question is clearly about a different service (order-api, debt-collection, …), say so explicitly — kloc cannot answer cross-service questions in the current setup.

## Three-layer flow

```
NAKED QUESTION (from analyst)
        │
        ▼
[Layer 1: Decomposition]                  ← skill: decompose
   • Translate biz jargon to code symbols
   • Detect ambiguity → STOP if true
   • Detect adversarial-suspected
   • Produce structured plan
        │
        ▼
[Layer 2: Retrieval]                      ← skill: kloc-mcp
   • Resolve symbols (kloc_resolve)
   • Semantic search (kloc_search) for concepts
   • Usages / flows / context per question type
   • Pull source for high-risk facts via kloc_source / kloc_chunks
        │
        ▼
[Layer 3: Synthesis]
   • Compose answer in the language the analyst used (PL/EN)
   • Cite file:line for every concrete claim
   • Distinguish "verified" from "inferred"
   • Surface gaps and limitations
```

## When to use

ALWAYS when a non-developer asks about code. Do not answer from prior knowledge or memory — always run the flow even if the answer "seems obvious."

## Step-by-step procedure

### Step 1 — Decompose (ALWAYS)

Apply the `decompose` skill. Produce the YAML plan. Show it to yourself (not necessarily to the user, unless the user explicitly wants to see the plan).

If `ambiguity_detected=true`:
- Output the clarifying questions to the user.
- STOP. Do not proceed to retrieval. Wait for user response.

If `requires_runtime_data=true`:
- Tell the user up front: this needs DB/metrics data, not just code.
- Offer to: (a) generate the SQL/query for them, (b) proceed with code-only answer noting the gap.

If `adversarial_suspected=true`:
- Adjust retrieval mindset: actively seek evidence of ABSENCE, not just presence.
- Required keyword searches must be tried; if all return empty → strong signal "not in repo."

### Step 2 — Retrieve (apply the `kloc-mcp` skill)

Follow the decision tree in the `kloc-mcp` skill. General order:

1. `kloc_resolve` on every symbol in `domain_translation` and `search_targets.symbol_patterns`.
2. `kloc_search` on `keyword_patterns` if symbol resolution failed or for behavior questions.
3. Per `question_type`:
   - **lookup** → `kloc_resolve` + `kloc_source` (NOT `kloc_explain` on Enums — returns error)
   - **rules/contracts** → `kloc_resolve` + `kloc_source` + `kloc_usages` for callsite recall
   - **behavior** → `kloc_search` (collection=both) + ⭐ `kloc_context` (with `include_impl=true` for interfaces) when you need to understand a class as a whole + `kloc_explain` on classes (verify FQN first!) + `kloc_flows` for inventory. **Default to `kloc_context` over `kloc_usages+kloc_deps`** — one call replaces both AND adds the definition block + edge metadata.
   - **location** → `kloc_resolve` + `kloc_usages`
   - **impact** → ⭐ `kloc_context depth=2 include_impl=true limit=200` — single best call for impact analysis
   - **history** → out of scope for kloc; tell the user to ask via git history
   - **data** → out of scope; produce SQL/Mongo query, don't invent counts
   - **adversarial** → if zero hits in `kloc_search` (top scores < 0.6) AND no matching flow in `kloc_flows` → answer "not found"

4. **Full-flow / sequence questions (Q10-style)**: `kloc_flows` detail mode currently returns **empty `triggers_in`/`triggers_out`**. Reconstruct the chain manually using `kloc_context` / `kloc_usages` between flow entries. Don't rely on `kloc_flows` alone for end-to-end behavior.

5. For high-risk questions (`hallucination_risk: high|very_high`), ALWAYS confirm with `kloc_source` — never paraphrase critical fields/values from `kloc_explain` alone, especially because `kloc_explain` silently coerces partial matches to the wrong symbol.

6. **Score interpretation for `kloc_search`**: ≥0.78 strong, 0.70-0.78 solid, 0.63-0.70 ADJACENT (probably not the answer), <0.60 noise. Tests inflate scores by ~0.05 — discount.

### Step 3 — Synthesize

Write the answer with this structure:

```
[Direct answer to the question, in the user's language]

[Optional: short explanation of context if descriptive question]

Źródła / Sources:
- file/path.php:line — what this confirms
- another/file.php:line — what this confirms

[Optional: "Czego nie udało się ustalić" / "Gaps" — be explicit]
```

Rules:
- **Cite file:line for every concrete claim.** No claim without a citation.
- **No invented identifiers.** Class names, enum values, field names, status names — all must come from retrieved content. If you didn't see it, don't write it.
- **Distinguish verified from inferred.** "Z kodu wynika X" vs "Prawdopodobnie Y, ale nie potwierdzone."
- **Match the user's register.** If the question is a quick one-liner from a stakeholder, the answer is two sentences plus a link. If it's a spec for a ticket, the answer is structured with sections.
- **Surface adversarial findings.** If you ran the flow and the feature doesn't exist, say so plainly: "Tej funkcjonalności nie ma w repo kyc. Sprawdzono X, Y, Z — wszystkie zwróciły zero. Może być w osobnym serwisie." Don't bury this.
- **For runtime-data questions:** never invent counts/averages. Produce the query and explain how to run it.

## Retrieval reflex — what to reach for first

Before calling any tool, ask: **"Do I need to understand a class/interface as a unit?"**

- **Yes** → start with `kloc_context` (with `include_impl=true` for interfaces). It returns usages + deps + definition + edge metadata in one shot. Use this BEFORE `kloc_usages` / `kloc_deps` / `kloc_source` unless you've already pinpointed exactly what you need.
- **No, I want one specific dimension** → use the narrower tool:
  - Just callsite recall (e.g., enum values) → `kloc_usages`
  - Just source text → `kloc_source`
  - Just one-sentence summary → `kloc_explain`
  - Just inventory of flows → `kloc_flows`
  - Just concept search → `kloc_search`

If you find yourself making 2-3 narrow calls on the same symbol, you almost certainly should have started with one `kloc_context` call.

**Reading code rule of thumb:** If you have an FQN (from `kloc_resolve` / `kloc_search` / `kloc_context`), use `kloc_source` — NOT `file_read`. `kloc_source` is symbol-scoped, returns token estimate, and doesn't dump the entire file. Reach for `file_read` only for whole files (configs, YAML, XML) or files kloc doesn't index. For large classes use `kloc_chunks` (method-boundary chunks).

## Anti-hallucination checklist (run before sending the answer)

- [ ] Every class/enum/field name in the answer was returned by a tool call.
- [ ] Every concrete claim has a file:line citation.
- [ ] No phrases like "the system probably…" without a corresponding "Gaps" note.
- [ ] If question was adversarial and feature absent — said so explicitly.
- [ ] If user asked in Polish, answered in Polish; same for English.
- [ ] No "fluff" sections ("In summary…", "I hope this helps…").

## Output to the user — defaults

Default to a **terse answer + sources block**, unless the user signals they want more depth ("napisz mi spec", "wytłumacz krok po kroku", "full report"). Analysts mostly want fast answers they can verify, not essays.

## Examples — full mini-runs

### Run A — simple lookup (PL)

User: "Jakie powody odrzucenia weryfikacji mamy w systemie?"

Layer 1 plan: rules, no ambiguity, high hallucination risk, `domain_translation: VerificationSessionRejectionReason`.

Layer 2 calls:
- `kloc_resolve("VerificationSessionRejectionReason")` → file:line confirmed
- `kloc_source(...)` → 5 enum values
- `kloc_usages(...)` → mapping value→callsite for each; INVALID_DATA has 0 callsites in production code (only in ESB mapper)

Layer 3 answer:
> 5 powodów (`VerificationSessionRejectionReason`, src/Verification/Domain/ValueObject/VerificationSessionRejectionReason.php:9):
> - **IDENTT_REJECTED** — gdy Identt odrzuca dokumenty (3 callsite'y w `*ProcessStrategy`)
> - **RDO_REJECTED** — gdy RDO odrzuca lub limit retry przekroczony (`AbstractProcessStrategy.php:258`)
> - **UNMATCHED** — niezgodność danych klient ↔ dokument (6 callsite'ów)
> - **CUSTOMER_BLOCKLISTED** — klient na blockliście (sprawdzane przy initialize)
> - **INVALID_DATA** — istnieje w enumie, ale **brak callsite'ów ustawiających go jako reason**; tylko w `VerificationSessionRejectionReasonMapper`. Wygląda na martwy / kompatybilność wsteczna.

### Run B — ambiguous (PL)

User: "Jak działa weryfikacja?"

Layer 1 plan: ambiguous, stop.

Output to user:
> Potrzebuję doprecyzowania:
> - Której fazy: inicjalizacji, processingu, wyniku?
> - Której metody: Identt video, mObywatel, IdentityDocumentUpdate?
> - Z perspektywy kodu czy użytkownika?

### Run C — adversarial (PL)

User: "Jak przetwarzamy zgłoszenia utraty dokumentu z aplikacji mobilnej?"

Layer 1: adversarial_suspected=true.

Layer 2:
- `kloc_search("lost stolen document mobile app report")` → top score 0.44, low signal
- `kloc_flows` → no public-facing flow for user-initiated loss report

Layer 3 answer:
> Nie znalazłem tej funkcjonalności w repo `kyc`. W systemie istnieje obsługa `lost_or_stolen` ale jest to **status zwracany przez Identt** (system-to-system), nie zgłoszenie od użytkownika. Brak public-facing endpointu przyjmującego takie zgłoszenia od klienta.
>
> Sprawdzone: `kloc_search` (top score 0.44), `kloc_flows type=http`. Wszystkie zero match.
>
> Funkcjonalność może być w innym serwisie. Sprawdź u dewa.
