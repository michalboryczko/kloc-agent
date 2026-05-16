"use client";

import { useMemo } from "react";
import type { SessionListItem } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type BusyState =
  | { kind: "none" }
  | { kind: "new" }
  | { kind: "pick"; id: string };

export type SessionPickerProps = {
  sessions: SessionListItem[] | null;
  error: string | null;
  busy: BusyState;
  onPick: (s: SessionListItem) => void;
  onNew: () => void;
};

export function SessionPicker({
  sessions,
  error,
  busy,
  onPick,
  onNew,
}: SessionPickerProps) {
  const sessionCount = sessions?.length ?? 0;
  const eyebrow = "text-[10.5px] font-mono uppercase tracking-[0.18em] text-[var(--text-dim)]";
  const isBusy = busy.kind !== "none";

  const formattedSessions = useMemo(
    () =>
      sessions?.map((s) => ({
        ...s,
        updatedLabel: new Date(s.updated_at).toLocaleString(),
      })) ?? null,
    [sessions],
  );

  return (
    <main className="mx-auto max-w-[620px] px-6 pt-20 pb-12">
      <p
        className={cn(
          eyebrow,
          "mb-6 motion-safe:animate-kloc-fade-up-delay-1",
        )}
      >
        — analyst chat
      </p>

      <h1 className="font-serif italic text-[64px] leading-[1.05] tracking-[-0.02em] text-[var(--text)] motion-safe:animate-kloc-fade-up">
        kloc <span>agent</span>
        <span className="text-[var(--accent)]">.</span>
      </h1>

      <p className="mt-6 text-[15px] text-[var(--text-mute)] motion-safe:animate-kloc-fade-up-delay-1">
        Resume a previous chat or start a new one.
      </p>

      <div className="mt-8 flex items-center gap-4 motion-safe:animate-kloc-fade-up-delay-2">
        <Button
          variant="primary"
          onClick={onNew}
          disabled={isBusy}
          className={isBusy ? "cursor-wait" : ""}
        >
          {busy.kind === "new" ? "Starting…" : "+ Start new chat"}
        </Button>
        {sessions !== null && (
          <span className="font-mono text-[12.5px] text-[var(--text-dim)]">
            {sessionCount} session{sessionCount === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {error && (
        <p className="mt-4 text-[13px] text-[var(--danger)]">
          failed to load sessions: {error}
        </p>
      )}

      {sessions === null && !error && (
        <p className="mt-4 text-[13px] text-[var(--text-mute)]">loading sessions…</p>
      )}

      {sessions !== null && sessions.length === 0 && !error && (
        <p className="mt-4 text-[13px] text-[var(--text-mute)]">
          No previous sessions. Click <strong>Start new chat</strong> above.
        </p>
      )}

      {formattedSessions !== null && formattedSessions.length > 0 && (
        <div className="motion-safe:animate-kloc-fade-up-delay-3">
          <p className={cn(eyebrow, "mt-12 mb-2")}>— recent</p>
          <ul className="divide-y divide-[var(--line)] border-t border-[var(--line)]">
            {formattedSessions.map((s) => {
              const isThisPicking =
                busy.kind === "pick" && busy.id === s.id;
              return (
                <li key={s.id}>
                  <button
                    type="button"
                    onClick={() => onPick(s)}
                    disabled={isBusy}
                    className={cn(
                      "group flex w-full items-start gap-3 px-1 py-3 text-left transition-transform duration-[160ms] ease-[var(--ease-out-snappy)]",
                      isThisPicking
                        ? "cursor-wait"
                        : isBusy
                          ? "disabled:cursor-not-allowed"
                          : "cursor-pointer hover:translate-x-[3px] hover:bg-[var(--bg-1)]",
                      "focus:outline-none focus-visible:bg-[var(--bg-1)]",
                    )}
                    aria-busy={isThisPicking}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[14px] font-medium text-[var(--text)] truncate">
                        {s.title || "Untitled session"}
                        {isThisPicking && (
                          <span className="ml-2 text-[var(--text-mute)]">loading…</span>
                        )}
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 text-[12px] text-[var(--text-mute)]">
                        <span>
                          {s.message_count} msg{s.message_count === 1 ? "" : "s"}
                        </span>
                        <span aria-hidden>·</span>
                        <span>{s.updatedLabel}</span>
                      </div>
                    </div>
                    <span className="font-mono text-[11.5px] text-[var(--text-dim)] mt-0.5 shrink-0">
                      {s.id.slice(0, 8)}
                    </span>
                    <span
                      aria-hidden
                      className="ml-1 text-[var(--accent)] opacity-0 transition-opacity duration-[120ms] ease-[var(--ease-out-snappy)] group-hover:opacity-100 mt-0.5"
                    >
                      →
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </main>
  );
}
