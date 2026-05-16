"use client";

import { useCoAgent } from "@copilotkit/react-core";
import { cn } from "@/lib/utils";

type RunnerState = "fresh" | "warm" | "evicted" | "crashed";

type KlocAgentState = {
  session_id?: string;
  runner_state?: RunnerState;
  artifacts?: Array<{ id: string; filename: string }>;
};

const AGENT_NAME =
  process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ?? "kloc_agent";

const EYEBROW =
  "font-mono text-[10.5px] uppercase tracking-[0.18em] text-[var(--text-dim)]";

const RUNNER_STATE_STYLE: Record<RunnerState, { dot: string; label: string }> = {
  fresh: {
    dot: "bg-[var(--success)] shadow-[0_0_8px_rgba(74,222,128,0.5)]",
    label: "text-[var(--success)]",
  },
  warm: {
    dot: "bg-[var(--success)] shadow-[0_0_8px_rgba(74,222,128,0.5)]",
    label: "text-[var(--success)]",
  },
  evicted: {
    dot: "bg-[var(--danger)]",
    label: "text-[var(--danger)]",
  },
  crashed: {
    dot: "bg-[var(--danger)]",
    label: "text-[var(--danger)]",
  },
};

export type SessionRailProps = {
  sessionId: string;
};

export function SessionRail({ sessionId }: SessionRailProps) {
  const { state } = useCoAgent<KlocAgentState>({
    name: AGENT_NAME,
    initialState: { artifacts: [] },
  });

  const runnerState = state?.runner_state;
  const artifacts = state?.artifacts ?? [];

  return (
    <aside
      className="hidden md:flex md:flex-col gap-8 border-r border-[var(--line)] bg-[var(--bg-1)]/40 px-5 py-6 overflow-y-auto"
      aria-label="Session details"
    >
      <SessionCard sessionId={sessionId} />
      <RunnerStateSection runnerState={runnerState} />
      <ArtifactsSection artifacts={artifacts} />
    </aside>
  );
}

function SessionCard({ sessionId }: { sessionId: string }) {
  return (
    <section>
      <p className={cn(EYEBROW, "mb-2")}>Session</p>
      <div
        className="relative rounded-lg border border-[var(--line)] bg-[var(--bg-2)] px-3 py-2.5 pl-4"
      >
        <span
          aria-hidden
          className="absolute left-0 top-0 h-full w-[2px] rounded-l-lg bg-[var(--accent)]"
        />
        <p className={cn(EYEBROW, "mb-1")}>Session ID</p>
        <p className="font-mono text-[12.5px] text-[var(--text)] break-all">
          {sessionId}
        </p>
      </div>
    </section>
  );
}

function RunnerStateSection({ runnerState }: { runnerState: RunnerState | undefined }) {
  const style = runnerState
    ? RUNNER_STATE_STYLE[runnerState]
    : { dot: "bg-[var(--text-dim)]", label: "text-[var(--text-dim)]" };
  const label = runnerState ?? "unknown";

  return (
    <section>
      <p className={cn(EYEBROW, "mb-2")}>Runner state</p>
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-full border border-[var(--line-strong)] bg-[var(--bg-2)] px-2.5 py-1 font-mono text-[10.5px] uppercase tracking-[0.14em]",
          style.label,
        )}
        aria-live="polite"
      >
        <span
          aria-hidden
          className={cn("inline-block h-1.5 w-1.5 rounded-full", style.dot)}
        />
        {label}
      </span>
    </section>
  );
}

function ArtifactsSection({
  artifacts,
}: {
  artifacts: Array<{ id: string; filename: string }>;
}) {
  return (
    <section className="flex-1 min-h-0 flex flex-col">
      <p className={cn(EYEBROW, "mb-2")}>Artifacts</p>
      {artifacts.length === 0 ? (
        <p className="font-serif italic text-[13px] text-[var(--text-dim)]">
          No artifacts yet.
        </p>
      ) : (
        <ul className="space-y-1 overflow-y-auto font-mono text-[12.5px] text-[var(--text-mute)]">
          {artifacts.map((a) => (
            <li key={a.id} className="truncate">
              <span aria-hidden className="text-[var(--text-dim)]">▪ </span>
              {a.filename}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
