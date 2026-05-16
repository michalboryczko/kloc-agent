"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  ApiError,
  NetworkError,
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

function isAbortError(e: unknown): boolean {
  return (
    e instanceof DOMException && e.name === "AbortError"
  ) || (e instanceof Error && e.name === "AbortError");
}

function formatError(e: unknown): string {
  if (e instanceof ApiError) {
    const preview = e.body.length > 120 ? `${e.body.slice(0, 120)}…` : e.body;
    return `backend ${e.status}: ${preview || e.message}`;
  }
  if (e instanceof NetworkError) {
    return "network unreachable — is the backend running?";
  }
  return e instanceof Error ? e.message : String(e);
}

export default function HomePage() {
  const [picked, setPicked] = useState<PickedSession | null>(null);
  const [sessions, setSessions] = useState<SessionListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  // Tracks the in-flight user-pick fetch (listMessages / createSession) so a
  // back-navigation or rapid retry cancels the previous request.
  const pickCtrlRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (picked) return;
    const ctrl = new AbortController();
    listSessions({ signal: ctrl.signal })
      .then((res) => {
        setSessions(res.sessions);
      })
      .catch((e: unknown) => {
        if (isAbortError(e)) return;
        setError(formatError(e));
      });
    return () => ctrl.abort();
  }, [picked]);

  function abortPick() {
    if (pickCtrlRef.current) {
      pickCtrlRef.current.abort();
      pickCtrlRef.current = null;
    }
  }

  async function pickExisting(s: SessionListItem) {
    setError(null);
    abortPick();
    const ctrl = new AbortController();
    pickCtrlRef.current = ctrl;
    setBusyId(s.id);
    try {
      const page = await listMessages(s.id, { limit: 500, signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setPicked({ sessionId: s.id, initialMessages: page.messages });
    } catch (e) {
      if (isAbortError(e)) return;
      setError(formatError(e));
    } finally {
      if (pickCtrlRef.current === ctrl) {
        pickCtrlRef.current = null;
        setBusyId(null);
      }
    }
  }

  async function startNew() {
    setError(null);
    abortPick();
    const ctrl = new AbortController();
    pickCtrlRef.current = ctrl;
    setBusyId("__new__");
    try {
      const r = await createSession({ signal: ctrl.signal });
      if (ctrl.signal.aborted) return;
      setPicked({ sessionId: r.session_id, initialMessages: [] });
    } catch (e) {
      if (isAbortError(e)) return;
      setError(formatError(e));
    } finally {
      if (pickCtrlRef.current === ctrl) {
        pickCtrlRef.current = null;
        setBusyId(null);
      }
    }
  }

  function onBack() {
    setError(null);
    abortPick();
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
