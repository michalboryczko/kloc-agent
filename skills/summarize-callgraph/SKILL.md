---
name: summarize-callgraph
description: "Produce a concise summary of the call graph for a PayPo Symfony handler, service, EventSubscriber, MessageHandler or controller. Use when the analyst asks how a piece of business logic is wired together, what calls what, or what an event handler invokes. Output is a short bulleted list of the main call edges with verbatim FQN references and terminal side-effects called out."
---

# Summarize Callgraph

When the analyst asks how a given handler, service, EventSubscriber, or
MessageHandler in a PayPo PHP/Symfony codebase (kyc, order-api, …) flows
through the code, follow this procedure.

## Procedure

1. **Resolve the entrypoint symbol.** Typical PayPo shapes:
   - `App\Verification\Application\MessageHandler\InitializeVerificationSessionHandler::__invoke`
   - `App\Verification\Application\EventSubscriber\EmitMessageOnEsbEventSubscriber::onIdentityVerified`
   - `App\Verification\UserInterface\Controller\InitVerificationController::__invoke`
   If the analyst gave a fuzzy name, run `kloc_search` (collection=both)
   to find candidate FQNs, then `kloc_resolve` to anchor exactly one and
   confirm `root.fqn`.
2. **Retrieve the call neighbourhood.** Use `kloc_context` (`depth=1`,
   `include_impl=true`) — it returns usages + deps + definition + edge
   metadata in one call. Bump to `depth=2` only if the immediate edges
   don't answer the question. For interfaces / abstracts, `include_impl`
   is mandatory.

## Output format

3-7 bullets, ordered by execution flow:

- **edge**: `<caller FQN>` → `<callee FQN>` (file:line) — one sentence
  on *why*.
- Final bullet — *terminal effects*: DB writes (entity persists, raw
  SQL, Doctrine flushes), ESB publishes (`MessageBusInterface::dispatch`
  → contract class), outbound HTTP (HttpClient calls), domain events
  emitted (`EventDispatcherInterface::dispatch`). The analyst usually
  cares more about these than the in-between plumbing.

## When to escalate

If the call graph is wider than ~10 edges, do NOT list them all.
Summarise the *clusters* (e.g. "this handler fans out into 3
sub-services: `PaymentProcessor`, `OrderRepository`,
`NotificationDispatcher`") and offer to drill down on whichever cluster
the analyst names next. For full-flow questions (HTTP → Message → ESB)
prefer `kloc_flows` first to inventory entries, then walk between flows
with `kloc_context`.

## What not to do

- Do NOT speculate about behaviour not visible in the graph.
- Do NOT invent classes or methods; if the tool returned nothing, say so
  plainly (adversarial honesty).
- Do NOT include test code or fixtures unless the analyst asks for them.
