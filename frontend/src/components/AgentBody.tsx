"use client";

import { useCoAgent, useCopilotAction } from "@copilotkit/react-core";
import type { Message as ApiMessage } from "@/lib/api";
import { ToolCallCard } from "@/components/ToolCallCard";

type KlocAgentState = {
  session_id?: string;
  runner_state?: "fresh" | "warm" | "evicted" | "crashed";
  artifacts?: Array<{ id: string; filename: string }>;
};

const AGENT_NAME =
  process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent";

type AgentBodyProps = {
  initialMessages?: ApiMessage[];
};

export function AgentBody({ initialMessages }: AgentBodyProps) {
  const { state } = useCoAgent<KlocAgentState>({
    name: AGENT_NAME,
    initialState: { artifacts: [] },
  });

  const priorCount = initialMessages?.length ?? 0;

  useCopilotAction({
    name: "*",
    render: ({
      name,
      args,
      status,
      result,
    }: {
      name: string;
      args: Record<string, unknown>;
      status: string;
      result?: unknown;
    }) => (
      <ToolCallCard
        name={name}
        args={args ?? {}}
        status={status}
        result={result}
      />
    ),
  });

  return (
    <section className="flex-1 p-6 text-sm leading-relaxed text-[var(--text-mute)]">
      <p>
        Ask a question about the indexed PHP codebase. The chat sidebar will
        stream the agent&apos;s reasoning, MCP tool calls, and final answer.
      </p>
      {priorCount > 0 && (
        <p className="mt-3 rounded-md border border-[var(--accent-line)] bg-[var(--accent-soft)] px-3 py-2 text-[13px] text-[var(--text)]">
          Resumed session — {priorCount} prior message
          {priorCount === 1 ? "" : "s"} will appear in the chat once you send
          your next message.
        </p>
      )}
      {state?.artifacts && state.artifacts.length > 0 && (
        <ul className="mt-4 space-y-1 font-mono text-[12.5px] text-[var(--text-mute)]">
          {state.artifacts.map((a) => (
            <li key={a.id}>{a.filename}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
