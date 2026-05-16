import { randomUUID } from "node:crypto";

export type IncomingMessage = {
  id?: string;
  role: string;
  content?: unknown;
  // ag-ui-protocol 0.1.18 message shape is loose at this layer; the backend
  // re-parses via pydantic.
  [key: string]: unknown;
};

export type IncomingBody = {
  threadId?: string;
  runId?: string;
  messages?: IncomingMessage[];
  tools?: unknown[];
  context?: unknown[];
  state?: Record<string, unknown>;
  forwardedProps?: Record<string, unknown>;
  properties?: Record<string, unknown>;
  lastEventId?: string;
};

export type ValidationResult =
  | { ok: true; body: IncomingBody }
  | { ok: false; reason: string };

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/**
 * Structural shape-check on the agent-proxy POST body. AG-UI's RunAgentInput
 * contract drives the field set; this function is the trust boundary — once
 * it returns ok, route.ts assumes the field types are correct.
 */
export function validateIncomingBody(value: unknown): ValidationResult {
  if (!isPlainObject(value)) {
    return { ok: false, reason: "body must be a JSON object" };
  }

  if (value.threadId !== undefined && typeof value.threadId !== "string") {
    return { ok: false, reason: "threadId must be a string" };
  }
  if (value.runId !== undefined && typeof value.runId !== "string") {
    return { ok: false, reason: "runId must be a string" };
  }
  if (value.lastEventId !== undefined && typeof value.lastEventId !== "string") {
    return { ok: false, reason: "lastEventId must be a string" };
  }

  if (value.messages !== undefined) {
    if (!Array.isArray(value.messages)) {
      return { ok: false, reason: "messages must be an array" };
    }
    for (let i = 0; i < value.messages.length; i++) {
      const m = value.messages[i];
      if (!isPlainObject(m)) {
        return { ok: false, reason: `messages[${i}] must be an object` };
      }
      if (typeof m.role !== "string") {
        return { ok: false, reason: `messages[${i}].role must be a string` };
      }
      if (m.id !== undefined && typeof m.id !== "string") {
        return { ok: false, reason: `messages[${i}].id must be a string when present` };
      }
    }
  }

  if (value.tools !== undefined && !Array.isArray(value.tools)) {
    return { ok: false, reason: "tools must be an array" };
  }
  if (value.context !== undefined && !Array.isArray(value.context)) {
    return { ok: false, reason: "context must be an array" };
  }

  for (const k of ["state", "forwardedProps", "properties"] as const) {
    const v = value[k];
    if (v !== undefined && !isPlainObject(v)) {
      return { ok: false, reason: `${k} must be an object when present` };
    }
  }

  return { ok: true, body: value as IncomingBody };
}

/**
 * Pick session_id out of properties / forwardedProps / state.
 * CopilotKit identifies the agent's session indirectly via forwardedProps;
 * the page bootstraps a session and threads the id through useCoAgent.
 */
export function resolveSessionId(body: IncomingBody): string | null {
  const props = body.properties ?? {};
  const fwd = body.forwardedProps ?? {};
  const state = body.state ?? {};
  const candidate =
    (props["session_id"] as string | undefined) ??
    (fwd["session_id"] as string | undefined) ??
    (fwd["sessionId"] as string | undefined) ??
    (state["session_id"] as string | undefined);
  return candidate ?? null;
}

/**
 * AG-UI requires every message to carry a stable id. Synthesise UUIDs for
 * any caller-supplied messages that arrive without one.
 */
export function ensureMessageIds(
  messages: IncomingMessage[] | undefined,
): IncomingMessage[] {
  if (!messages) return [];
  return messages.map((m) => ({
    ...m,
    id: m.id && m.id.length > 0 ? m.id : randomUUID(),
  }));
}
