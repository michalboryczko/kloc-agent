"use client";

// Inline chat surface — alternative to <CopilotSidebar> on `page.tsx`.
//
// Provides a full-page chat layout that consumers can drop into a route
// when they don't want the pinned sidebar. CopilotChat handles all the
// streaming, message rendering, and tool-call cards via the runtime.

import { CopilotChat } from "@copilotkit/react-ui";

export function ChatWindow({
  title = "kloc analyst",
  initial = "Ask anything about the indexed PHP codebase.",
}: {
  title?: string;
  initial?: string;
}) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <CopilotChat
        labels={{ title, initial }}
        instructions="You are kloc, an analyst assistant over a PHP codebase indexed by kloc-intelligence. Use the available MCP tools to answer questions about the code."
      />
    </div>
  );
}
