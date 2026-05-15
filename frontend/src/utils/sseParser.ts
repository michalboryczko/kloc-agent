// Minimal SSE line-buffered parser.
//
// Reads a `ReadableStream<Uint8Array>` and yields one parsed JSON object per
// `data:` frame. Trailing partial chunks are buffered until the next `\n\n`
// terminator arrives. Lifted from
// `sample-strands-agent-with-agentcore/frontend/src/utils/sseParser.ts`
// (research/05-reference-projects.md, Repo 2) and simplified.
//
// Not on the CopilotKit hot path — `@copilotkit/runtime` does its own
// parsing via `@ag-ui/client`. Kept for fallback / future non-Copilot
// pages.

export type SseEvent = {
  event?: string;
  id?: string;
  data: unknown;
};

export async function* parseSse(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseEvent, void, void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let separatorIdx: number;
      // SSE frames are terminated by a blank line (`\n\n` or `\r\n\r\n`).
      while ((separatorIdx = nextSeparator(buffer)) !== -1) {
        const frame = buffer.slice(0, separatorIdx);
        buffer = buffer.slice(separatorIdx + 2);
        const parsed = parseFrame(frame);
        if (parsed) yield parsed;
      }
    }
    // Flush any trailing complete frame.
    if (buffer.length > 0) {
      const parsed = parseFrame(buffer);
      if (parsed) yield parsed;
    }
  } finally {
    reader.releaseLock();
  }
}

function nextSeparator(buf: string): number {
  // Prefer `\n\n` because that's what the backend writes; fall back to CRLF
  // just in case a proxy normalises line endings.
  const lf = buf.indexOf("\n\n");
  if (lf !== -1) return lf;
  const crlf = buf.indexOf("\r\n\r\n");
  return crlf;
}

function parseFrame(frame: string): SseEvent | null {
  let eventName: string | undefined;
  let id: string | undefined;
  const dataLines: string[] = [];

  for (const rawLine of frame.split(/\r?\n/)) {
    const line = rawLine.trimEnd();
    if (line.length === 0 || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    if (colon === -1) continue;
    const field = line.slice(0, colon);
    const value = line.slice(colon + 1).replace(/^ /, "");
    switch (field) {
      case "event":
        eventName = value;
        break;
      case "id":
        id = value;
        break;
      case "data":
        dataLines.push(value);
        break;
      default:
        // Unknown SSE field; ignore per spec.
        break;
    }
  }

  if (dataLines.length === 0) return null;
  const dataText = dataLines.join("\n");
  try {
    return { event: eventName, id, data: JSON.parse(dataText) };
  } catch {
    return { event: eventName, id, data: dataText };
  }
}
