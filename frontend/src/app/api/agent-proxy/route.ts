// agent-proxy: AG-UI envelope builder + SSE forwarder.
//
// Reads CopilotKit's POST body, validates shape, builds a canonical
// RunAgentInput envelope, and forwards to FastAPI's
// POST /v1/sessions/{session_id}/stream with Accept: text/event-stream.
// The SSE response is streamed back to the browser unmodified.

import { randomUUID } from "node:crypto";
import type { NextRequest } from "next/server";
import {
  ensureMessageIds,
  resolveSessionId,
  validateIncomingBody,
} from "./validation";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const BACKEND_URL =
  process.env.BACKEND_URL ??
  process.env.NEXT_PUBLIC_BACKEND_URL ??
  "http://localhost:8000";

const AGENT_NAME = process.env.COPILOTKIT_AGENT_NAME ?? "kloc_agent";

const UPSTREAM_TIMEOUT_MS = 30_000;

export const GET = async () => {
  // CopilotRuntime probes agent metadata on registration; return the
  // configured name so the runtime registry can discover it.
  return Response.json({ id: AGENT_NAME, name: AGENT_NAME });
};

export const POST = async (req: NextRequest) => {
  let parsed: unknown;
  try {
    parsed = await req.json();
  } catch {
    return Response.json(
      { error: "agent-proxy: invalid JSON body" },
      { status: 400 },
    );
  }

  const result = validateIncomingBody(parsed);
  if (!result.ok) {
    return Response.json(
      { error: `agent-proxy: invalid body: ${result.reason}` },
      { status: 400 },
    );
  }

  const body = result.body;
  const sessionId = resolveSessionId(body);
  if (!sessionId) {
    if (process.env.NEXT_PUBLIC_DEBUG_HTTP === "true") {
      // Diagnostic: emit only key names, never values. Body state/forwardedProps
      // can contain user-supplied text or secrets in misconfigured clients.
      console.warn("[agent-proxy] no session_id; keys=", {
        body: Object.keys(body),
        state: Object.keys(body.state ?? {}),
        forwardedProps: Object.keys(body.forwardedProps ?? {}),
      });
    }
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

  // Combine the client-disconnect signal with a 30s timeout so a slow
  // backend cannot tie up a Node worker indefinitely.
  const signal = AbortSignal.any([
    req.signal,
    AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
  ]);

  let upstream: Response;
  try {
    upstream = await fetch(url.toString(), {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(runAgentInput),
      signal,
    });
  } catch (err) {
    // AbortError covers both client-disconnect and the timeout case.
    const isAbort = err instanceof Error && err.name === "AbortError";
    const isTimeout = req.signal.aborted ? false : isAbort;
    if (isTimeout) {
      console.error("[agent-proxy] upstream timeout", {
        url: url.toString(),
        timeoutMs: UPSTREAM_TIMEOUT_MS,
      });
      return Response.json(
        { error: "agent-proxy: upstream timeout" },
        { status: 504 },
      );
    }
    if (isAbort) {
      // Client went away before upstream responded — nothing to send back.
      return new Response(null, { status: 499 });
    }
    console.error("[agent-proxy] upstream fetch failed", {
      url: url.toString(),
      error: err instanceof Error ? err.message : String(err),
    });
    return Response.json(
      { error: "agent-proxy: upstream unreachable" },
      { status: 502 },
    );
  }

  if (!upstream.ok || !upstream.body) {
    // Drain upstream body for server logs (truncated) but never echo it
    // back to the browser — backend stack traces / debug HTML would land
    // in the chat UI.
    const preview = await upstream
      .text()
      .then((t) => t.slice(0, 256))
      .catch(() => "");
    console.error("[agent-proxy] upstream rejected", {
      status: upstream.status,
      url: url.toString(),
      preview,
    });
    return Response.json(
      {
        error: "agent-proxy: backend rejected",
        status: upstream.status,
      },
      { status: upstream.status },
    );
  }

  // Forward the SSE body verbatim. The browser's EventSource consumer
  // sees the same wire format the backend produced.
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
