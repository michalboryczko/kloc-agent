// CopilotKit Next.js route handler.
//
// The handler wires the CopilotRuntime to an HttpAgent pointed at our
// in-process `/api/agent-proxy` route. The proxy is the glue layer that
// translates CopilotKit's runtime calls into AG-UI's `RunAgentInput`
// envelope and streams the SSE response back. The runtime never talks
// to the FastAPI backend directly.
//
// Lifted from CopilotKit/CopilotKit @ examples/integrations/strands-python
// (research/05-reference-projects.md, Repo 4).

import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { type AbstractAgent, HttpAgent } from "@ag-ui/client";
import type { NextRequest } from "next/server";

const AGENT_NAME = process.env.COPILOTKIT_AGENT_NAME ?? "kloc_agent";

// In dev / docker compose, the proxy is co-located with the Next.js server.
// `HttpAgent` POSTs `RunAgentInput`, then iterates SSE events off the body.
function proxyUrl(req: NextRequest): string {
  const origin = req.nextUrl.origin;
  return `${origin}/api/agent-proxy`;
}

const serviceAdapter = new ExperimentalEmptyAdapter();

export const POST = async (req: NextRequest) => {
  const agents: Record<string, AbstractAgent> = {
    [AGENT_NAME]: new HttpAgent({ url: proxyUrl(req) }),
  };
  // CopilotKit 1.52.1's `agents` type intersects `T` and `Promise<T>` in
  // their `MaybePromise<NonEmptyRecord<T>>` constraint — unsatisfiable at
  // compile time without a cast. The constructor accepts the plain Record
  // at runtime (Repo 4 demo confirms). Remove the cast when 1.53+ lands.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const runtime = new CopilotRuntime({ agents: agents as any });

  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime,
    serviceAdapter,
    endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
};
