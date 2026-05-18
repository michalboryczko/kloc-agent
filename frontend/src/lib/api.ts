import type {
  CreateSessionResponse,
  MessagesPage,
  SessionDetail,
  SessionList,
} from "./types";

export const BROWSER_BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      try {
        body = await res.text();
      } catch {
        body = null;
      }
    }
    throw new ApiError(
      `HTTP ${res.status} ${res.statusText}`,
      res.status,
      body,
    );
  }
  return (await res.json()) as T;
}

export async function listSessions(opts?: {
  includeClosed?: boolean;
  signal?: AbortSignal;
}): Promise<SessionList> {
  const params = new URLSearchParams();
  if (opts?.includeClosed) params.set("include_closed", "true");
  const qs = params.toString();
  const url = `${BROWSER_BACKEND_URL}/v1/sessions${qs ? `?${qs}` : ""}`;
  const res = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal: opts?.signal,
  });
  return jsonOrThrow<SessionList>(res);
}

export async function createSession(body?: {
  title?: string;
}): Promise<CreateSessionResponse> {
  const res = await fetch(`${BROWSER_BACKEND_URL}/v1/sessions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  return jsonOrThrow<CreateSessionResponse>(res);
}

export async function getSession(
  id: string,
  opts?: { signal?: AbortSignal },
): Promise<SessionDetail> {
  const res = await fetch(`${BROWSER_BACKEND_URL}/v1/sessions/${id}`, {
    method: "GET",
    headers: { Accept: "application/json" },
    signal: opts?.signal,
  });
  return jsonOrThrow<SessionDetail>(res);
}

export async function listMessages(
  id: string,
  params?: { after?: number; limit?: number; signal?: AbortSignal },
): Promise<MessagesPage> {
  const qs = new URLSearchParams();
  if (params?.after !== undefined) qs.set("after", String(params.after));
  qs.set("limit", String(params?.limit ?? 500));
  const res = await fetch(
    `${BROWSER_BACKEND_URL}/v1/sessions/${id}/messages?${qs.toString()}`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: params?.signal,
    },
  );
  return jsonOrThrow<MessagesPage>(res);
}

export async function closeSession(id: string): Promise<void> {
  const res = await fetch(`${BROWSER_BACKEND_URL}/v1/sessions/${id}/close`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    throw new ApiError(
      `HTTP ${res.status} ${res.statusText}`,
      res.status,
      null,
    );
  }
}

export async function cancelRun(
  sessionId: string,
  runId: string,
): Promise<void> {
  const res = await fetch(
    `${BROWSER_BACKEND_URL}/v1/sessions/${sessionId}/runs/${runId}/cancel`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  if (!res.ok) {
    throw new ApiError(
      `HTTP ${res.status} ${res.statusText}`,
      res.status,
      null,
    );
  }
}

export function artifactDownloadUrl(artifactId: string): string {
  return `${BROWSER_BACKEND_URL}/v1/artifacts/${artifactId}`;
}
