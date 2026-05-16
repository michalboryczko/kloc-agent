"use client";

import { useCopilotAction } from "@copilotkit/react-core";
import { ToolCallCard } from "@/components/ToolCallCard";

type ToolRenderProps = {
  name: string;
  args: Record<string, unknown>;
  status: string;
  result?: unknown;
};

// Module-scope component so the render reference is stable across renders.
// Inline arrow functions defeat CopilotKit's internal memoization paths.
function RenderToolCall({ name, args, status, result }: ToolRenderProps) {
  return (
    <ToolCallCard
      name={name}
      args={args ?? {}}
      status={status}
      result={result}
    />
  );
}

/**
 * Registers the wildcard tool-call renderer. Renders nothing itself —
 * mounted inside <CopilotKit> so streamed tool calls flow through ToolCallCard.
 */
export function AgentBody() {
  useCopilotAction({
    name: "*",
    render: RenderToolCall,
  });

  return null;
}
