// agent-proxy: the AG-UI envelope builder + SSE forwarder.
//
// CopilotKit's `HttpAgent` POSTs a raw call shape (with `messages`, `tools`,
// `context`, `state`, `forwardedProps`) — it does NOT natively speak
// AG-UI's `RunAgentInput`. This proxy:
//
//   1. Reads CopilotKit's POST body.
//   2. Ensures every message has a UUID id (AG-UI requirement).
//   3. Builds a canonical `RunAgentInput` (threadId, runId, messages, tools,
//      context, state, forwardedProps).
//   4. POSTs it to the FastAPI backend's
//      `POST /v1/sessions/{session_id}/stream` with
//      `Accept: text/event-stream`.
//   5. Streams the SSE response back to the browser unmodified.
//
// Reconnect: if the request body carries a `last_event_id` (the AG-UI
// resume cursor), it's appended as a URL parameter so the backend can
// replay the tail of the run before resuming live emission.
//
// Lifted from CopilotKit/CopilotKit @ examples/integrations/strands-python
// (research/05-reference-projects.md, Repo 4). Adapted for our
// `/v1/sessions/{id}/stream` URL shape (Contract A) and our
// `session_id`-bearing `forwardedProps`.

import { randomUUID } from "node:crypto";
import type { NextRequest } from "next/server";

const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_BACKEND_URL ??
  "http://localhost:8000";

const AGENT_NAME = process.env.COPILOTKIT_AGENT_NAME ?? "kloc_agent";

type IncomingMessage = {
  id?: string;
  role: string;
  content?: unknown;
  // ag-ui-protocol 0.1.18 message shape is loose at this layer; the backend
  // re-parses via pydantic.
  [key: string]: unknown;
};

type IncomingBody = {
  threadId?: string;
  runId?: string;
  messages?: IncomingMessage[];
  tools?: unknown[];
  context?: unknown[];
  state?: Record<string, unknown>;
  forwardedProps?: Record<string, unknown>;
  lastEventId?: string;
};

// CopilotKit identifies the agent by name in `forwardedProps`; we also use
// it to discover the backend `session_id` (the page bootstraps a session and
// passes the id to `useCoAgent`'s state).
function resolveSessionId(body: IncomingBody): string | null {
  const fwd = body.forwardedProps ?? {};
  const state = body.state ?? {};
  const candidate =
    (fwd["session_id"] as string | undefined) ??
    (fwd["sessionId"] as string | undefined) ??
    (state["session_id"] as string | undefined);
  return candidate ?? null;
}

function ensureMessageIds(messages: IncomingMessage[] | undefined): IncomingMessage[] {
  if (!messages) return [];
  return messages.map((m) => ({
    ...m,
    id: m.id && m.id.length > 0 ? m.id : randomUUID(),
  }));
}

export const GET = async () => {
  // CopilotRuntime probes the agent metadata on registration. We return the
  // configured name so it can be discovered by the runtime registry.
  return Response.json({ id: AGENT_NAME, name: AGENT_NAME });
};

export const POST = async (req: NextRequest) => {
  let body: IncomingBody;
  try {
    body = (await req.json()) as IncomingBody;
  } catch {
    return Response.json(
      { error: "agent-proxy: invalid JSON body" },
      { status: 400 },
    );
  }

  const sessionId = resolveSessionId(body);
  if (!sessionId) {
    return Response.json(
      {
        error:
          "agent-proxy: no session_id in forwardedProps or state. " +
          "The page must call POST /v1/sessions before chatting.",
      },
      { status: 400 },
    );
  }

  const threadId = body.threadId ?? sessionId;
  const runId = body.runId ?? randomUUID();

  // Canonical AG-UI 0.1.18 RunAgentInput shape. The JS SDK and Repo 4's
  // proxy use camelCase; ag-ui-protocol's Pydantic model uses snake_case
  // but is configured with field aliases (ConfiguredBaseModel) so either
  // form deserialises. If the backend rejects this, switch to snake_case
  // (thread_id, run_id, forwarded_props) — keep this as the trip-wire.
  const runAgentInput = {
    threadId,
    runId,
    messages: ensureMessageIds(body.messages),
    tools: body.tools ?? [],
    context: body.context ?? [],
    state: body.state ?? {},
    forwardedProps: { ...(body.forwardedProps ?? {}), session_id: sessionId },
  };

  const url = new URL(`/v1/sessions/${sessionId}/stream`, BACKEND_URL);
  if (body.lastEventId) {
    url.searchParams.set("last_event_id", body.lastEventId);
  }

  const upstream = await fetch(url.toString(), {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(runAgentInput),
    // Forward client disconnects so the backend can drop the SSE generator
    // and free runner resources.
    signal: req.signal,
  });

  if (!upstream.ok || !upstream.body) {
    const text = await upstream.text().catch(() => "");
    return new Response(
      `agent-proxy: backend rejected (${upstream.status}): ${text}`,
      {
        status: upstream.status,
        // Backend may return HTML (FastAPI debug pages), JSON, or plain
        // text. Force text/plain so the browser doesn't try to render
        // an error body as HTML inside the chat shell.
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      },
    );
  }

  // Forward the SSE body verbatim. The browser's EventSource/CopilotRuntime
  // consumes the same wire format the backend produced.
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
};
