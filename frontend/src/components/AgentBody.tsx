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

  // CopilotKit 1.56 has no public way to seed the sidebar's message
  // buffer (the documented `useCopilotChat({ initialMessages })` is not
  // wired through, and `useAgent.setMessages()` populates v2's store
  // which the v1 `<CopilotSidebar>` doesn't read). The runner's first
  // MESSAGES_SNAPSHOT brings prior history into the sidebar when the
  // user sends their next message — we surface a hint below so the
  // user knows what to expect.
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
    <section
      style={{
        flex: 1,
        padding: 24,
        fontSize: 14,
        opacity: 0.8,
        lineHeight: 1.5,
      }}
    >
      <p>
        Ask a question about the indexed PHP codebase. The chat sidebar will
        stream the agent&apos;s reasoning, MCP tool calls, and final answer.
      </p>
      {priorCount > 0 && (
        <p
          style={{
            marginTop: 12,
            padding: "8px 12px",
            background: "rgba(80, 130, 220, 0.08)",
            border: "1px solid rgba(80, 130, 220, 0.25)",
            borderRadius: 6,
            fontSize: 13,
          }}
        >
          Resumed session — {priorCount} prior message
          {priorCount === 1 ? "" : "s"} will appear in the chat once you send
          your next message.
        </p>
      )}
      {state?.artifacts && state.artifacts.length > 0 && (
        <ul>
          {state.artifacts.map((a) => (
            <li key={a.id}>{a.filename}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
