---
name: decompose
description: Decompose a raw business-analyst question into a retrieval plan BEFORE searching the codebase. Use as the FIRST step whenever a non-technical user asks a question about code. Translates business jargon to code concepts, detects ambiguity, flags adversarial/runtime questions, and produces a structured plan for downstream retrieval. Triggers on any natural-language question about how the code works, what fields exist, why something is done, etc., asked by someone who isn't a developer on the project.
---

# Decomposition (Layer 1)

## When to use

Whenever a question arrives from a **business analyst, PM, QA, or stakeholder** about a PayPo PHP/Symfony codebase. Do NOT skip this step even if the question looks simple — many "simple" questions hide ambiguity ("jak działa weryfikacja?") or refer to functionality that lives in a different service or doesn't exist at all.

Skip only if:
- The user has already supplied a structured plan / explicit symbol names / file paths.
- The question is a follow-up clarification within the same session.

## Output format (mandatory)

Produce a YAML block with these keys. Do NOT skip keys; if not applicable, set to `null` or `false`.

```yaml
question_type:        # one of: lookup | behavior | location | rules | contracts |
                      # data | history | impact | ambiguous | adversarial-suspected
ambiguity_detected:   # true | false
clarifying_questions: # list[str]; only if ambiguity_detected=true. Stop and ask the user.
domain_translation:   # list[str]; explicit "biz term → code symbol/path" mappings
sub_questions:        # list[str]; atomic questions that retrieval can answer one-by-one
search_targets:
  file_patterns:      # list[str]; concrete globs, NOT "src/**"
  symbol_patterns:    # list[str]; class/enum/method names to resolve
  keyword_patterns:   # list[str]; strings to grep/embed-search
  source_types:       # list of: code | config | db-schema | contracts | vcs | docs | db-data
expected_answer_shape: # deterministic | structural | descriptive | open
risk_flags:
  hallucination_risk:  # low | medium | high | very_high
  adversarial_suspected: # true | false  (functionality may not exist)
  cross_repo:          # true | false  (answer may need other services)
  requires_runtime_data: # true | false (DB query / metrics needed)
out_of_scope:         # list[str]; aspects of the question that the code cannot answer
notes_for_synthesis:  # list[str]; constraints for the final-answer writer
                      #   (e.g., "do not invent fields", "recall > precision")
```

## Rules

1. **Never answer the question.** Your job is to enable the next layer, not produce content.
2. **Never read files or call tools that retrieve content.** Resolving symbols (`kloc_resolve`) is allowed only to map biz terms to FQNs in `domain_translation`. No `kloc_source`, `kloc_explain`, `file_read`, structural reads.
3. **Translate jargon explicitly.** "Rumunia" → `country=RO` → `config/services/ro/`. Write it in `domain_translation`. Never do this mapping silently in your head.
4. **Treat ambiguity as a hard stop.** If `ambiguity_detected=true`, return the plan with clarifying questions and STOP — let the user answer before proceeding.
5. **List multiple source types.** A question about "how X works" usually needs code + config + maybe contracts. Don't lock onto one.
6. **Search targets must be concrete.** "Wszędzie w domenie" is a bug. `src/Verification/Domain/Service/**/*Blocklist*` is correct.
7. **Adversarial flag.** If the question mentions a feature with vague provenance ("mobile app", "AML scoring", "loyalty program") and you don't have prior evidence it exists in this repo, set `adversarial_suspected=true`. This will tell retrieval to look for ABSENCE evidence, not just presence.
8. **Runtime vs code.** Questions about "how many", "average", "current state" are `requires_runtime_data=true` — code can't answer them. Flag and surface to user.
9. **No invented values.** Never put concrete enum values, field names, or numbers in the plan. The plan describes WHERE to look, not WHAT is there.

## Common pitfalls (instant-fail signals)

- Returning a long narrative answer instead of a structured plan.
- Missing `adversarial_suspected` on a question about non-existent features.
- Writing `search_targets.file_patterns: ["src/**"]`.
- Skipping `domain_translation` when the question uses business words ("klient", "Rumunia", "blocklista", "weryfikacja").
- Answering an ambiguous question with a guess instead of asking the user.

## Example invocations

### Q: "Z jakich powodów odrzucamy weryfikację?"

```yaml
question_type: rules
ambiguity_detected: false
domain_translation:
  - "powody odrzucenia weryfikacji" → enum VerificationSessionRejectionReason
search_targets:
  file_patterns:
    - src/Verification/Domain/ValueObject/VerificationSessionRejectionReason*.php
    - src/Verification/Application/ProcessStrategy/**
    - src/Verification/Application/EventSubscriber/**
  symbol_patterns: [VerificationSessionRejectionReason]
  keyword_patterns: ["::rejected(", "RejectionReason::"]
  source_types: [code]
sub_questions:
  - Jakie wartości ma enum VerificationSessionRejectionReason?
  - Dla każdej wartości — w którym miejscu kodu jest ustawiana?
  - Czy są wartości enuma BEZ callsite'ów (dead enum)?
expected_answer_shape: structural
risk_flags:
  hallucination_risk: high
  adversarial_suspected: false
  cross_repo: false
  requires_runtime_data: false
notes_for_synthesis:
  - "Recall > precision — przegapienie reasonu = błąd audytowy"
  - "Wymień każdą wartość enuma; oznacz wyraźnie te bez callsite'ów"
```

### Q: "Jak działa weryfikacja?"

```yaml
question_type: ambiguous
ambiguity_detected: true
clarifying_questions:
  - "Której fazy: inicjalizacji, processingu, wyniku?"
  - "Której metody: Identt video, mObywatel, IdentityDocumentUpdate?"
  - "Z perspektywy kodu czy użytkownika końcowego?"
domain_translation: []
sub_questions: []
search_targets: {}
expected_answer_shape: open
risk_flags:
  hallucination_risk: high
notes_for_synthesis:
  - "STOP — czekaj na odpowiedź usera. Nie zgaduj intencji."
```

### Q: "Jak przetwarzamy zgłoszenia utraty dokumentu z aplikacji mobilnej?"

```yaml
question_type: adversarial-suspected
ambiguity_detected: false
domain_translation:
  - "utrata/kradzież" → grep: lost, stolen, utrac, kradz
  - "zgłoszenia przez klienta przez aplikację mobilną" → user-initiated public endpoint
search_targets:
  file_patterns:
    - src/IdentityDocument/UserInterface/Controller/**
    - src/Verification/UserInterface/Controller/**
    - src/**/*Notification*
  keyword_patterns: [lost, stolen, utrac, kradz, lost_or_stolen, report]
  source_types: [code, contracts]
sub_questions:
  - Czy istnieje public-facing endpoint dla user-initiated zgłoszeń utraty?
  - Czy IdentityDocumentNotification jest user-initiated czy systemowa?
  - Czy są ślady planowania (TODO, komentarze)?
expected_answer_shape: open
risk_flags:
  hallucination_risk: very_high
  adversarial_suspected: true
notes_for_synthesis:
  - "Jeśli funkcjonalność nie istnieje — POWIEDZ TO WPROST."
  - "Halucynacja endpointu = test failed."
out_of_scope:
  - "Funkcjonalność może być w osobnym serwisie (sprawdź założenia z userem)"
```
