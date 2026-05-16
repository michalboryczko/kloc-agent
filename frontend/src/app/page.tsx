"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import {
  createSession,
  listMessages,
  listSessions,
  type Message,
  type SessionListItem,
} from "@/lib/api";
import { SessionPicker } from "@/components/SessionPicker";

// CopilotKit (~1.5 MB) only loads on the chat view; the picker route ships
// without it. ssr:false because CopilotKit hooks are client-only.
const ChatShell = dynamic(
  () => import("@/components/ChatShell").then((m) => m.ChatShell),
  { ssr: false },
);

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
    setError(null);
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
    setError(null);
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

  function onBack() {
    setError(null);
    setPicked(null);
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
    <ChatShell
      sessionId={picked.sessionId}
      initialMessages={picked.initialMessages}
      onBack={onBack}
    />
  );
}
