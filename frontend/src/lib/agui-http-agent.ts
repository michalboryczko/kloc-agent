// Direct AG-UI HttpAgent wiring.
//
// This is the non-CopilotKit codepath — useful for pages that talk to the
// kloc-agent backend without the CopilotKit runtime in between (e.g. raw
// transcript viewers, e2e harnesses, future non-chat surfaces). The main
// flow (`/api/copilotkit` → `HttpAgent` → `/api/agent-proxy`) does NOT
// import this; see `src/app/api/copilotkit/route.ts` for that path.

import { HttpAgent } from "@ag-ui/client";

const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export function createDirectAgent(sessionId: string): HttpAgent {
  // POSTs to /v1/sessions/{id}/stream directly (server-side use only — the
  // browser would need a server-side proxy to avoid CORS / cookie issues).
  return new HttpAgent({
    url: `${BACKEND_URL}/v1/sessions/${sessionId}/stream`,
  });
}
