import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { type AbstractAgent, HttpAgent } from "@ag-ui/client";
import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const AGENT_NAME = process.env.COPILOTKIT_AGENT_NAME ?? "kloc_agent";

// Same-origin proxy. Building a relative URL lets us hoist HttpAgent to
// module scope: there is no per-request value to capture.
const agent = new HttpAgent({ url: "/api/agent-proxy" });

const agents: Record<string, AbstractAgent> = { [AGENT_NAME]: agent };

// TODO(copilotkit-1.53+): CopilotKit 1.56.5's `agents` type intersects T
// and Promise<T> in MaybePromise<NonEmptyRecord<T>>; the constraint is
// unsatisfiable at compile time. Drop the cast when 1.53+ relaxes it.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const copilotRuntime = new CopilotRuntime({ agents: agents as any });

const serviceAdapter = new ExperimentalEmptyAdapter();

const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
  runtime: copilotRuntime,
  serviceAdapter,
  endpoint: "/api/copilotkit",
});

export const POST = (req: NextRequest) => handleRequest(req);
