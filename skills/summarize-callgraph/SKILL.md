---
name: summarize-callgraph
description: "Produce a concise summary of the call graph for a Symfony handler or service. Use when the analyst asks how a piece of business logic is wired together, what calls what, or what an event handler invokes. Output is a short bulleted list of the main call edges with FQN references."
---

# Summarize Callgraph

When the analyst asks about how a given handler, service, or event listener
flows through the codebase, follow this procedure:

1. Identify the entrypoint symbol from the user's question. Common forms:
   - `App\Order\OrderPlacedHandler::__invoke`
   - `App\Service\PaymentProcessor::charge`
   - a route handler like `App\Controller\OrderController::placeOrder`
2. Use the `kloc_context` MCP tool to retrieve the immediate call neighbourhood
   for that symbol (depth 1 by default; expand to 2 if the answer is thin).
3. Use `kloc_search` if the user gave a fuzzy symbol name — search by FQN
   substring and pick the most plausible entrypoint.
4. Produce 3-7 bullets following this template:
   - **edge**: `<caller FQN>` → `<callee FQN>` — one sentence on *why*.
   - End with one bullet identifying *terminal effects* (DB writes, external
     HTTP, message bus publishes) so the analyst sees side-effects clearly.
5. Cite every FQN verbatim — do NOT shorten class names or omit namespaces.

## When to escalate

If the call graph is wider than ~10 edges, do NOT list them all. Instead
summarise the *clusters* (e.g. "this handler fans out into 3 sub-services:
PaymentProcessor, OrderRepository, NotificationDispatcher") and offer to
drill down on whichever cluster the analyst names next.

## What not to do

- Do NOT speculate about behaviour not visible in the call graph.
- Do NOT invent classes or methods; if the tool returns nothing, say so.
- Do NOT include test code or fixtures unless the analyst asks for them.
