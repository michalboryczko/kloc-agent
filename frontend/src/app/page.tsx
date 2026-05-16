"use client";

import { useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotChat } from "@copilotkit/react-ui";
import {
  createSession,
  listMessages,
  listSessions,
  type Message,
  type SessionListItem,
} from "@/lib/api";
import { A11yChatTextarea } from "@/components/A11yChatTextarea";
import { AgentBody } from "@/components/AgentBody";
import { SessionPicker } from "@/components/SessionPicker";
import { SessionRail } from "@/components/SessionRail";
import { Button } from "@/components/ui/button";
import { AGENT_NAME } from "@/lib/config";

type PickedSession = {
  sessionId: string;
  initialMessages: Message[];
};

export default function HomePage() {
  const [picked, setPicked] = useState<PickedSession | null>(null);
  const [sessions, setSessions] = useState<SessionListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (picked) return;
    let cancelled = false;
    listSessions()
      .then((res) => {
        if (!cancelled) setSessions(res.sessions);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [picked]);

  async function pickExisting(s: SessionListItem) {
    setBusyId(s.id);
    try {
      const page = await listMessages(s.id, { limit: 500 });
      setPicked({ sessionId: s.id, initialMessages: page.messages });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function startNew() {
    setBusyId("__new__");
    try {
      const r = await createSession();
      setPicked({ sessionId: r.session_id, initialMessages: [] });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  if (!picked) {
    return (
      <SessionPicker
        sessions={sessions}
        error={error}
        busyId={busyId}
        onPick={pickExisting}
        onNew={startNew}
      />
    );
  }

  return (
    <main className="grid grid-rows-[48px_1fr] h-screen">
      <header className="sticky top-0 z-10 col-span-full flex h-12 items-center gap-4 border-b border-[var(--line-strong)] bg-[var(--bg-1)]/70 px-6 backdrop-blur">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setPicked(null)}
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
          session {picked.sessionId.slice(0, 8)}…
        </h2>
      </header>

      <CopilotKit
        runtimeUrl="/api/copilotkit"
        agent={AGENT_NAME}
        showDevConsole={false}
        enableInspector={false}
        threadId={picked.sessionId}
        properties={{ session_id: picked.sessionId }}
      >
        <AgentBody />
        <div className="grid grid-cols-1 min-[880px]:grid-cols-[280px_1fr] min-h-0 overflow-hidden">
          <SessionRail sessionId={picked.sessionId} />
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
