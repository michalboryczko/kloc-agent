"use client";

import { useEffect, useState } from "react";
import { CopilotKit } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui";
import {
  createSession,
  listMessages,
  listSessions,
  type Message,
  type SessionListItem,
} from "@/lib/api";
import { AgentBody } from "@/components/AgentBody";

const AGENT_NAME =
  process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent";

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
    <main>
      <header
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid rgba(120, 120, 120, 0.2)",
          display: "flex",
          alignItems: "center",
          gap: 16,
        }}
      >
        <button
          type="button"
          onClick={() => setPicked(null)}
          style={{
            padding: "6px 10px",
            border: "1px solid rgba(120, 120, 120, 0.35)",
            borderRadius: 6,
            background: "transparent",
            cursor: "pointer",
            fontSize: 12,
          }}
        >
          ← sessions
        </button>
        <div>
          <h1 style={{ margin: 0, fontSize: 20 }}>kloc-agent</h1>
          <p style={{ margin: 0, opacity: 0.7, fontSize: 13 }}>
            session: {picked.sessionId}
          </p>
        </div>
      </header>

      <CopilotKit
        runtimeUrl="/api/copilotkit"
        agent={AGENT_NAME}
        showDevConsole={false}
        enableInspector={false}
        threadId={picked.sessionId}
        properties={{ session_id: picked.sessionId }}
      >
        <AgentBody initialMessages={picked.initialMessages} />
        <CopilotSidebar
          defaultOpen={true}
          clickOutsideToClose={false}
          labels={{
            title: "kloc analyst",
            initial: "Ask anything about the indexed PHP codebase.",
          }}
        />
      </CopilotKit>
    </main>
  );
}

type PickerProps = {
  sessions: SessionListItem[] | null;
  error: string | null;
  busyId: string | null;
  onPick: (s: SessionListItem) => void;
  onNew: () => void;
};

function SessionPicker({
  sessions,
  error,
  busyId,
  onPick,
  onNew,
}: PickerProps) {
  return (
    <main style={{ maxWidth: 720, margin: "40px auto", padding: "0 24px" }}>
      <h1 style={{ marginTop: 0 }}>kloc-agent</h1>
      <p style={{ opacity: 0.75, fontSize: 14 }}>
        Resume a previous chat or start a new one.
      </p>

      <div style={{ marginTop: 20, marginBottom: 24 }}>
        <button
          type="button"
          onClick={onNew}
          disabled={busyId !== null}
          style={{
            padding: "10px 16px",
            border: "1px solid rgba(120, 120, 120, 0.4)",
            borderRadius: 8,
            background: "rgba(80, 130, 220, 0.12)",
            cursor: busyId !== null ? "wait" : "pointer",
            fontSize: 14,
            fontWeight: 600,
          }}
        >
          {busyId === "__new__" ? "Starting…" : "+ Start new chat"}
        </button>
      </div>

      {error && (
        <p style={{ color: "crimson", fontSize: 13 }}>
          failed to load sessions: {error}
        </p>
      )}

      {sessions === null && !error && (
        <p style={{ opacity: 0.6, fontSize: 13 }}>loading sessions…</p>
      )}

      {sessions !== null && sessions.length === 0 && (
        <p style={{ opacity: 0.6, fontSize: 13 }}>
          No previous sessions. Click <strong>Start new chat</strong> above.
        </p>
      )}

      {sessions !== null && sessions.length > 0 && (
        <ul
          style={{
            listStyle: "none",
            padding: 0,
            margin: 0,
            borderTop: "1px solid rgba(120, 120, 120, 0.15)",
          }}
        >
          {sessions.map((s) => (
            <li
              key={s.id}
              style={{
                borderBottom: "1px solid rgba(120, 120, 120, 0.15)",
              }}
            >
              <button
                type="button"
                onClick={() => onPick(s)}
                disabled={busyId !== null}
                style={{
                  display: "block",
                  width: "100%",
                  padding: "12px 4px",
                  background: "transparent",
                  border: "none",
                  textAlign: "left",
                  cursor: busyId !== null ? "wait" : "pointer",
                  fontSize: 14,
                  color: "inherit",
                }}
              >
                <div style={{ fontWeight: 500 }}>
                  {s.title || "Untitled session"}
                  {busyId === s.id && (
                    <span style={{ opacity: 0.6, marginLeft: 8 }}>
                      loading…
                    </span>
                  )}
                </div>
                <div style={{ opacity: 0.6, fontSize: 12, marginTop: 2 }}>
                  {s.message_count} message{s.message_count === 1 ? "" : "s"}
                  {" · "}
                  {new Date(s.updated_at).toLocaleString()}
                  {" · "}
                  <span style={{ fontFamily: "monospace" }}>{s.id.slice(0, 8)}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
