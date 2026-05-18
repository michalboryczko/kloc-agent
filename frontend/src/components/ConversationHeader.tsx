"use client";

import Link from "next/link";
import { ThemeToggle } from "./ThemeToggle";

export function ConversationHeader({
  title,
  sessionId,
  messageCount,
  closed,
}: {
  title: string;
  sessionId: string;
  messageCount: number;
  closed: boolean;
}) {
  return (
    <header className="flex items-center justify-between px-5 py-3 border-b border-[--color-line] shrink-0">
      <div className="flex items-center gap-3 min-w-0">
        <Link
          href="/"
          data-test="back-btn"
          aria-label="Back to sessions"
          className="p-1 -ml-1 rounded hover:bg-[--color-canvas-sunk] text-[--color-ink-muted]"
        >
          <svg
            width={16}
            height={16}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.6}
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
        </Link>
        <div className="min-w-0">
          <h1
            data-test="thread-title"
            className="text-[14px] font-medium leading-tight truncate"
          >
            {title}
          </h1>
          <p className="mono text-[11px] text-[--color-ink-muted] mt-0.5">
            <span data-test="thread-id">{sessionId}</span>
            <span aria-hidden> · </span>
            <span>{messageCount} messages</span>
            {closed && (
              <>
                <span aria-hidden> · </span>
                <span
                  data-test="closed-session-indication"
                  className="text-[--color-warning]"
                >
                  CLOSED
                </span>
              </>
            )}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0 text-[--color-ink-muted]">
        <ThemeToggle />
      </div>
    </header>
  );
}
