"use client";

import Link from "next/link";
import type { SessionListItem as SessionListItemModel } from "@/lib/types";
import { formatRelative } from "@/lib/time";
import { cn } from "@/lib/cn";

export function SessionListItem({
  session,
  active,
}: {
  session: SessionListItemModel;
  active: boolean;
}) {
  return (
    <Link
      href={`/s/${session.id}`}
      data-test="session-item"
      data-active={active ? "true" : undefined}
      aria-current={active ? "page" : undefined}
      className={cn(
        "w-full text-left px-2.5 py-2 rounded-md transition-colors block",
        active
          ? "bg-[--color-canvas] ring-1 ring-[--color-line-strong] shadow-[0_1px_0_rgba(0,0,0,0.02)]"
          : "hover:bg-[--color-canvas]/60",
      )}
    >
      <div className="flex items-center gap-2 mb-0.5">
        {active && (
          <span
            className="w-1.5 h-1.5 rounded-full bg-[--color-accent] shrink-0 pulse-dot"
            aria-hidden
          />
        )}
        <span
          data-test="session-title"
          className="text-[13px] font-medium truncate text-[--color-ink]"
        >
          {session.title}
        </span>
      </div>
      <div
        className={cn(
          "flex items-center gap-1.5 text-[11px] text-[--color-ink-muted]",
          active && "pl-3.5",
        )}
      >
        <span data-test="session-id" className="mono">
          {session.id.slice(0, 7)}
        </span>
        <span aria-hidden>·</span>
        <span data-test="session-count">{session.message_count} msgs</span>
        <span data-test="session-when" className="ml-auto mono">
          {formatRelative(session.updated_at)}
        </span>
      </div>
    </Link>
  );
}
