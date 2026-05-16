export const AGENT_NAME =
  process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ??
  process.env.COPILOTKIT_AGENT_NAME ??
  "kloc_agent";

export type Artifact = { id: string; filename: string };

export const INITIAL_AGENT_STATE: { artifacts: Artifact[] } = {
  artifacts: [],
};
