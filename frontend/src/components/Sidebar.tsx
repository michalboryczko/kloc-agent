"use client";

import { useEffect, useState } from "react";
import type { SessionListItem as SessionListItemModel } from "@/lib/types";
import { listSessions } from "@/lib/api";
import { isToday } from "@/lib/time";
import { NewSessionButton } from "./NewSessionButton";
import { SessionListItem } from "./SessionListItem";
import { EmptyState } from "./EmptyState";
import { ErrorBanner } from "./ErrorBanner";
import { RailFooter } from "./RailFooter";

type Group = { name: "Today" | "Earlier"; items: SessionListItemModel[] };

function groupSessions(sessions: SessionListItemModel[]): Group[] {
  const today: SessionListItemModel[] = [];
  const earlier: SessionListItemModel[] = [];
  for (const s of sessions) {
    if (isToday(s.updated_at)) today.push(s);
    else earlier.push(s);
  }
  const groups: Group[] = [];
  if (today.length) groups.push({ name: "Today", items: today });
  if (earlier.length) groups.push({ name: "Earlier", items: earlier });
  return groups;
}

export function Sidebar({ activeId }: { activeId?: string }) {
  const [sessions, setSessions] = useState<SessionListItemModel[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const ctrl = new AbortController();
    listSessions({ signal: ctrl.signal })
      .then((r) => {
        setSessions(r.sessions);
        setError(null);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string }).name === "AbortError") return;
        setError(e instanceof Error ? e.message : "Failed to load sessions");
        setSessions([]);
      });
    return () => ctrl.abort();
  }, [reloadKey]);

  const groups = sessions ? groupSessions(sessions) : [];

  return (
    <aside
      data-test="sidebar"
      className="w-[260px] shrink-0 flex flex-col bg-[--color-canvas-rail] border-r border-[--color-line]"
    >
      <div className="p-3 border-b border-[--color-line] space-y-3">
        <div className="flex items-center gap-2 px-1">
          <div className="w-6 h-6 rounded-[6px] bg-[--color-ink] grid place-items-center">
            <svg
              width={13}
              height={13}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.8}
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-white"
              aria-hidden
            >
              <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2Z" />
            </svg>
          </div>
          <span className="text-[13px] font-medium tracking-tight">kloc</span>
          <span className="mono text-[10px] text-[--color-ink-faint] ml-auto">
            v0.1
          </span>
        </div>
        <NewSessionButton />
      </div>

      <nav className="flex-1 overflow-y-auto scroll px-2 py-2 min-h-0">
        {error ? (
          <div className="px-1 py-2">
            <ErrorBanner
              message={`Failed to load sessions: ${error}`}
              onRetry={() => setReloadKey((k) => k + 1)}
            />
          </div>
        ) : sessions === null ? (
          <p className="mono text-[10.5px] text-[--color-ink-faint] px-2 py-2">
            Loading…
          </p>
        ) : sessions.length === 0 ? (
          <EmptyState message="No prior sessions. Start a new one to begin." />
        ) : (
          groups.map((group) => (
            <div key={group.name} className="mb-3">
              <p
                data-test="group-header"
                className="mono text-[9.5px] tracking-[0.12em] text-[--color-ink-faint] uppercase px-2 mb-1"
              >
                {group.name}
              </p>
              {group.items.map((s) => (
                <SessionListItem
                  key={s.id}
                  session={s}
                  active={s.id === activeId}
                />
              ))}
            </div>
          ))
        )}
      </nav>

      <RailFooter repo="mrandmrssmith/api" meta="indexed PHP codebase" />
    </aside>
  );
}
