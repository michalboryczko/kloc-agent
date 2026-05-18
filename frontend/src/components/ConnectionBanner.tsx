import type { ConnectionState } from "@/lib/types";

const VISIBLE: Record<ConnectionState, boolean> = {
  idle: false,
  live: false,
  connecting: true,
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
    "bg-[--color-canvas-sunk] border-[--color-line] text-[--color-ink-muted]",
  replaying:
    "bg-[--color-canvas-sunk] border-[--color-line] text-[--color-ink-muted]",
  offline:
    "bg-[--color-canvas-sunk] border-[--color-warning]/40 text-[--color-warning]",
  error:
    "bg-[--color-danger-bg] border-[--color-danger-line] text-[--color-danger-ink]",
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
