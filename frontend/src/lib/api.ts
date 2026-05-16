// Session lifecycle REST helpers.
//
// These wrap the JSON endpoints on the FastAPI backend (Contract A). The
// stream endpoint is consumed via `agent-proxy/route.ts` and not exposed
// here — CopilotKit drives streaming, not direct callers.
//
// Every helper accepts an optional `signal?: AbortSignal`. React hook
// callers manage controller lifecycle (effect cleanup, nav, dedup).

const BROWSER_BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export type SessionCreateResponse = {
  session_id: string;
  created_at: string;
};

export type SessionSummary = {
  id: string;
  status: "open" | "closed";
  runner_state: "fresh" | "warm" | "evicted" | "crashed";
  message_count: number;
  created_at: string;
  last_activity_at?: string;
};

export type SessionListItem = {
  id: string;
  title: string;
  runner_state: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
};

export type SessionList = {
  sessions: SessionListItem[];
};

export type Message = {
  id: string;
  seq: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  tool_calls?: unknown[];
  finalized_at?: string;
};

export type MessagesPage = {
  messages: Message[];
  next_cursor: string | null;
  has_more: boolean;
};

/**
 * Thrown for any non-2xx HTTP response from the backend. Carries the
 * raw status and body so callers can differentiate (e.g., 404 vs 500).
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: string;
  constructor(status: number, body: string, message?: string) {
    super(message ?? `kloc-agent ${status}: ${body || "request failed"}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/**
 * Thrown when the request never reached the backend (DNS failure,
 * connection refused, TLS error, etc.). Distinct from ApiError because
 * the UI surface differs: "network unreachable" vs "backend rejected".
 */
export class NetworkError extends Error {
  readonly cause?: unknown;
  constructor(message: string, cause?: unknown) {
    super(message);
    this.name = "NetworkError";
    this.cause = cause;
  }
}

function isAbortError(err: unknown): boolean {
  return (
    err instanceof DOMException && err.name === "AbortError"
  ) || (err instanceof Error && err.name === "AbortError");
}

async function safeFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    if (isAbortError(err)) {
      throw err;
    }
    throw new NetworkError(
      `network unreachable: ${err instanceof Error ? err.message : String(err)}`,
      err,
    );
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body);
  }
  return (await res.json()) as T;
}

export type RequestOptions = { signal?: AbortSignal };

export async function createSession(
  opts: RequestOptions = {},
): Promise<SessionCreateResponse> {
  const res = await safeFetch(`${BROWSER_BACKEND_URL}/v1/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
    signal: opts.signal,
  });
  return jsonOrThrow<SessionCreateResponse>(res);
}

export async function listSessions(
  options: { includeClosed?: boolean; signal?: AbortSignal } = {},
): Promise<SessionList> {
  const url = new URL(`/v1/sessions`, BROWSER_BACKEND_URL);
  if (options.includeClosed) url.searchParams.set("include_closed", "true");
  const res = await safeFetch(url.toString(), { signal: options.signal });
  return jsonOrThrow<SessionList>(res);
}

export async function getSession(
  id: string,
  opts: RequestOptions = {},
): Promise<SessionSummary> {
  const res = await safeFetch(`${BROWSER_BACKEND_URL}/v1/sessions/${id}`, {
    signal: opts.signal,
  });
  return jsonOrThrow<SessionSummary>(res);
}

export async function listMessages(
  id: string,
  options: { after?: string; limit?: number; signal?: AbortSignal } = {},
): Promise<MessagesPage> {
  const url = new URL(`/v1/sessions/${id}/messages`, BROWSER_BACKEND_URL);
  if (options.after) url.searchParams.set("after", options.after);
  if (options.limit) url.searchParams.set("limit", String(options.limit));
  const res = await safeFetch(url.toString(), { signal: options.signal });
  return jsonOrThrow<MessagesPage>(res);
}

export async function closeSession(
  id: string,
  opts: RequestOptions = {},
): Promise<void> {
  const res = await safeFetch(
    `${BROWSER_BACKEND_URL}/v1/sessions/${id}/close`,
    { method: "POST", signal: opts.signal },
  );
  if (!res.ok && res.status !== 204) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body);
  }
}
