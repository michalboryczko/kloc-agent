import type { ConnectionState } from "@/lib/types";

const VISIBLE: Record<ConnectionState, boolean> = {
  idle: false,
  live: false,
  connecting: false,
  replaying: true,
  offline: true,
  error: true,
};

const COPY: Record<ConnectionState, string> = {
  idle: "",
  live: "",
  connecting: "connecting…",
  replaying: "catching up…",
  offline: "reconnecting…",
  error: "assistant failed",
};

const TONE: Record<ConnectionState, string> = {
  idle: "",
  live: "",
  connecting:
    "bg-[var(--color-canvas-sunk)] border-[var(--color-line)] text-[var(--color-ink-muted)]",
  replaying:
    "bg-[var(--color-canvas-sunk)] border-[var(--color-line)] text-[var(--color-ink-muted)]",
  offline:
    "bg-[var(--color-canvas-sunk)] border-[var(--color-warning)]/40 text-[var(--color-warning)]",
  error:
    "bg-[var(--color-danger-bg)] border-[var(--color-danger-line)] text-[var(--color-danger-ink)]",
};

export function ConnectionBanner({
  state,
  errorMessage,
}: {
  state: ConnectionState;
  errorMessage: string | null;
}) {
  if (!VISIBLE[state]) return null;
  const body = state === "error" && errorMessage ? errorMessage : COPY[state];
  return (
    <div
      data-test="connection-banner"
      data-state={state}
      role={state === "error" ? "alert" : "status"}
      className={`mono text-[11px] px-3 py-1.5 border-b ${TONE[state]}`}
    >
      {body}
    </div>
  );
}
