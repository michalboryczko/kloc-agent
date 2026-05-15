"use client";

import { useEffect, useState } from "react";
import { useCoAgent, useCopilotAction } from "@copilotkit/react-core";
import { CopilotSidebar } from "@copilotkit/react-ui";
import { createSession } from "@/lib/api";
import { ToolCallCard } from "@/components/ToolCallCard";

type KlocAgentState = {
  session_id?: string;
  // Latest STATE_SNAPSHOT keys from the runner (kept loose for PoC).
  runner_state?: "fresh" | "warm" | "evicted" | "crashed";
  artifacts?: Array<{ id: string; filename: string }>;
};

export default function HomePage() {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  // Bootstrap a session on first load. The session_id is persisted in
  // localStorage so tab-reload reuses the same session. Two AC18 paths to
  // distinguish:
  //   (a) Mid-stream network drop (tab still open): CopilotKit's HttpAgent
  //       handles auto-resume — when its SSE connection drops it re-POSTs
  //       the same RunAgentInput with `lastEventId`, which the agent-proxy
  //       forwards as `?last_event_id=…`. Backend's ExecutionRegistry
  //       replays buffered events after the cursor. No frontend work
  //       beyond what the proxy already does.
  //   (b) Tab close then re-open: CopilotKit starts a fresh runtime, so
  //       the in-flight run is unrecoverable from the UI. The recommended
  //       PoC behaviour is: on cold mount, refetch persisted history via
  //       `GET /v1/sessions/{id}/messages` (REST, not SSE) and let the
  //       analyst keep chatting with the same session_id. CopilotKit
  //       1.52.1 doesn't expose a hook to seed history into the visible
  //       transcript, so this is wired below as state-mirror only — the
  //       full SSE-replay path is a follow-on for v2.
  useEffect(() => {
    let cancelled = false;
    const stored =
      typeof window !== "undefined"
        ? window.localStorage.getItem("kloc.session_id")
        : null;
    if (stored) {
      // v2 TODO: a stale id can survive in localStorage past the backend
      // session being closed (manual close, DB reset, etc.). Probing via
      // `getSession(stored)` and dropping on 404/closed before reuse is
      // the right fix; for the PoC we trust the id and surface a "failed
      // to open session" bootError if the next REST call rejects.
      setSessionId(stored);
      return;
    }
    createSession()
      .then((res) => {
        if (cancelled) return;
        setSessionId(res.session_id);
        window.localStorage.setItem("kloc.session_id", res.session_id);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setBootError(err instanceof Error ? err.message : String(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Shared state — bound to STATE_SNAPSHOT / STATE_DELTA on the AG-UI wire.
  const { state, setState } = useCoAgent<KlocAgentState>({
    name: process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent",
    initialState: { artifacts: [] },
  });

  // Once the session is bootstrapped, push the id into shared state so the
  // agent-proxy route picks it up via `state.session_id` (its resolveSessionId
  // fallback chain). Without this, the first POST out of CopilotKit lacks the
  // session_id and the proxy returns 400.
  useEffect(() => {
    if (sessionId) {
      setState((prev) => ({ ...prev, session_id: sessionId }));
    }
  }, [sessionId, setState]);

  // Generic generative-UI render for ALL server-tool calls (the PoC ships
  // one shape; per-tool customisations can layer on later). Name `"*"` is
  // CopilotKit's wildcard match — every TOOL_CALL_START/ARGS/END/RESULT
  // from the AG-UI stream lands here. This is also the surface the analyst
  // sees when `BeforeToolCallEvent` denies a tool (AC19): the
  // `TOOL_CALL_RESULT` event carries the denial reason, and `result` is
  // populated with that string in the `complete` status.
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
    <main>
      <header
        style={{
          padding: "16px 24px",
          borderBottom: "1px solid rgba(120, 120, 120, 0.2)",
        }}
      >
        <h1 style={{ margin: 0, fontSize: 20 }}>kloc-agent</h1>
        <p style={{ margin: 0, opacity: 0.7, fontSize: 13 }}>
          {sessionId
            ? `session: ${sessionId}`
            : bootError
              ? `failed to open session: ${bootError}`
              : "opening session…"}
        </p>
      </header>

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
        {state?.artifacts && state.artifacts.length > 0 && (
          <ul>
            {state.artifacts.map((a) => (
              <li key={a.id}>{a.filename}</li>
            ))}
          </ul>
        )}
      </section>

      {/* Gate the sidebar on session_id landing in shared state — otherwise
          CopilotKit can POST to /api/agent-proxy before the useEffect at
          page.tsx:73-77 mirrors sessionId into useCoAgent state, and the
          proxy returns 400 "no session_id". The bootstrap usually finishes
          in < 50ms so the gate is invisible in practice. */}
      {sessionId !== null && (
        <CopilotSidebar
          defaultOpen={true}
          clickOutsideToClose={false}
          labels={{
            title: "kloc analyst",
            initial: "Ask anything about the indexed PHP codebase.",
          }}
        />
      )}
    </main>
  );
}
