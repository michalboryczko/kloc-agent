export const AGENT_NAME =
  process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ??
  process.env.COPILOTKIT_AGENT_NAME ??
  "kloc_agent";

// Browser-side backend URL. NEXT_PUBLIC_ vars are inlined at build time.
// Server-side route handlers should use BACKEND_URL > NEXT_PUBLIC_BACKEND_URL
// instead — see agent-proxy/route.ts.
export const BROWSER_BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export type Artifact = { id: string; filename: string };

export const INITIAL_AGENT_STATE: { artifacts: Artifact[] } = {
  artifacts: [],
};
