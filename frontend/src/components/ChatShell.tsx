"use client";

import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import "@copilotkit/react-ui/styles.css";

import { A11yChatTextarea } from "@/components/A11yChatTextarea";
import { AgentBody } from "@/components/AgentBody";
import { SessionRail } from "@/components/SessionRail";
import { Button } from "@/components/ui/button";
import { AGENT_NAME } from "@/lib/config";
import type { Message } from "@/lib/api";

export type ChatShellProps = {
  sessionId: string;
  initialMessages: Message[];
  onBack: () => void;
};

export function ChatShell({ sessionId, onBack }: ChatShellProps) {
  return (
    <main className="grid grid-rows-[48px_1fr] h-screen">
      <header className="sticky top-0 z-10 col-span-full flex h-12 items-center gap-4 border-b border-[var(--line-strong)] bg-[var(--bg-1)]/70 px-6 backdrop-blur">
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          aria-label="Back to sessions"
        >
          ← sessions
        </Button>
        <h1 className="font-serif italic text-[20px] tracking-[-0.02em] text-[var(--text)] flex items-center">
          <span>kloc </span>
          <span className="text-[var(--accent)]">agent</span>
          <span className="ml-2 inline-flex h-4 items-center rounded-sm border border-[var(--line-strong)] px-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--text-dim)] not-italic">
            BETA
          </span>
        </h1>
        <h2 className="ml-auto font-mono text-[12px] text-[var(--text-mute)]">
          session {sessionId.slice(0, 8)}…
        </h2>
      </header>

      <CopilotKit
        runtimeUrl="/api/copilotkit"
        agent={AGENT_NAME}
        showDevConsole={false}
        enableInspector={false}
        threadId={sessionId}
        properties={{ session_id: sessionId }}
      >
        <AgentBody />
        <div className="grid grid-cols-1 min-[880px]:grid-cols-[280px_1fr] min-h-0 overflow-hidden">
          <SessionRail sessionId={sessionId} />
          <div className="chat-pane min-h-0 flex flex-col">
            <A11yChatTextarea label="Ask kloc analyst">
              <CopilotChat
                className="kloc-copilot-chat flex-1 min-h-0"
                labels={{
                  title: "kloc analyst",
                  initial: "Ask anything about the indexed PHP codebase.",
                }}
                instructions="You are kloc, an analyst assistant over a PHP codebase indexed by kloc-intelligence. Use the available MCP tools to answer questions about the code."
              />
            </A11yChatTextarea>
          </div>
        </div>
      </CopilotKit>
    </main>
  );
}

export default ChatShell;
