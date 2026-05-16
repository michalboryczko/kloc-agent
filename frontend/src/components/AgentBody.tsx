"use client";

import { useCopilotAction } from "@copilotkit/react-core";
import { ToolCallCard } from "@/components/ToolCallCard";

/**
 * Registers the wildcard tool-call renderer. Renders nothing itself —
 * mounted inside <CopilotKit> so streamed tool calls flow through ToolCallCard.
 */
export function AgentBody() {
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

  return null;
}
