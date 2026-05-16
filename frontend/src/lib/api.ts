// Session lifecycle REST helpers.
//
// These wrap the JSON endpoints on the FastAPI backend (Contract A). The
// stream endpoint is consumed via `agent-proxy/route.ts` and not exposed
// here — CopilotKit drives streaming, not direct callers.

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

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`kloc-agent ${res.status}: ${body || res.statusText}`);
  }
  return (await res.json()) as T;
}

export async function createSession(): Promise<SessionCreateResponse> {
  const res = await fetch(`${BROWSER_BACKEND_URL}/v1/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return jsonOrThrow<SessionCreateResponse>(res);
}

export async function listSessions(
  options: { includeClosed?: boolean } = {},
): Promise<SessionList> {
  const url = new URL(`/v1/sessions`, BROWSER_BACKEND_URL);
  if (options.includeClosed) url.searchParams.set("include_closed", "true");
  const res = await fetch(url.toString());
  return jsonOrThrow<SessionList>(res);
}

export async function getSession(id: string): Promise<SessionSummary> {
  const res = await fetch(`${BROWSER_BACKEND_URL}/v1/sessions/${id}`);
  return jsonOrThrow<SessionSummary>(res);
}

export async function listMessages(
  id: string,
  options: { after?: string; limit?: number } = {},
): Promise<MessagesPage> {
  const url = new URL(`/v1/sessions/${id}/messages`, BROWSER_BACKEND_URL);
  if (options.after) url.searchParams.set("after", options.after);
  if (options.limit) url.searchParams.set("limit", String(options.limit));
  const res = await fetch(url.toString());
  return jsonOrThrow<MessagesPage>(res);
}

export async function closeSession(id: string): Promise<void> {
  const res = await fetch(`${BROWSER_BACKEND_URL}/v1/sessions/${id}/close`, {
    method: "POST",
  });
  if (!res.ok && res.status !== 204) {
    const body = await res.text().catch(() => "");
    throw new Error(`kloc-agent ${res.status}: ${body || res.statusText}`);
  }
}
