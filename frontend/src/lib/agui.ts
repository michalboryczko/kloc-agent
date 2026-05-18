import type { AGUIEvent } from "./types";

export type StreamFrame =
  | { kind: "event"; event: AGUIEvent }
  | { kind: "end" };

export class StreamWindowExceededError extends Error {
  constructor() {
    super("recovery_window_exceeded");
    this.name = "StreamWindowExceededError";
  }
}

export class StreamRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "StreamRequestError";
  }
}

export interface RunStartArgs {
  sessionId: string;
  runId: string;
  messages: { role: string; content: string }[];
  signal?: AbortSignal;
}

export interface RunResumeArgs {
  sessionId: string;
  runId: string;
  lastEventId: number | null;
  signal?: AbortSignal;
}

export async function* openRunStream(
  args: RunStartArgs,
): AsyncGenerator<AGUIEvent> {
  const res = await fetch("/api/agent-proxy", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      sessionId: args.sessionId,
      runId: args.runId,
      run_id: args.runId,
      messages: args.messages,
    }),
    cache: "no-store",
    signal: args.signal,
  });
  if (!res.ok || !res.body) {
    throw new StreamRequestError(
      `stream POST failed: HTTP ${res.status}`,
      res.status,
    );
  }
  yield* parseSSEBody(res.body, args.signal);
}

export async function* resumeRunStream(
  args: RunResumeArgs,
): AsyncGenerator<AGUIEvent> {
  const qs = new URLSearchParams({
    session_id: args.sessionId,
    run_id: args.runId,
  });
  if (args.lastEventId !== null) {
    qs.set("last_event_id", String(args.lastEventId));
  }
  const res = await fetch(`/api/agent-proxy/resume?${qs.toString()}`, {
    method: "GET",
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
    signal: args.signal,
  });
  if (res.status === 404 || res.status === 410) {
    throw new StreamWindowExceededError();
  }
  if (!res.ok || !res.body) {
    throw new StreamRequestError(
      `resume GET failed: HTTP ${res.status}`,
      res.status,
    );
  }
  yield* parseSSEBody(res.body, args.signal);
}

async function* parseSSEBody(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<AGUIEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      if (signal?.aborted) return;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let blockEnd = buffer.indexOf("\n\n");
      while (blockEnd !== -1) {
        const block = buffer.slice(0, blockEnd);
        buffer = buffer.slice(blockEnd + 2);
        const evt = decodeBlock(block);
        if (evt !== null) yield evt;
        blockEnd = buffer.indexOf("\n\n");
      }
    }
    const tail = buffer.trim();
    if (tail) {
      const evt = decodeBlock(tail);
      if (evt !== null) yield evt;
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // intentionally swallowed: cancel-during-finally races with normal stream
      // completion and a thrown error here would mask the original cause.
    }
  }
}

function decodeBlock(block: string): AGUIEvent | null {
  const lines = block.split("\n");
  let data = "";
  for (const raw of lines) {
    const line = raw.startsWith("\r") ? raw.slice(1) : raw;
    if (line.startsWith(":")) continue;
    if (line.startsWith("data:")) {
      const payload = line.slice(5).trimStart();
      data = data ? `${data}\n${payload}` : payload;
    }
  }
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as AGUIEvent;
    return parsed;
  } catch {
    return null;
  }
}
