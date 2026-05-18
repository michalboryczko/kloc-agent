"use client";

import type { MessagesPage, SessionDetail } from "@/lib/types";

export function ConversationClient({
  initialDetail,
  initialHistory,
}: {
  initialDetail: SessionDetail;
  initialHistory: MessagesPage;
}) {
  const closed = initialDetail.closed_at !== null;
  const priorCount = initialHistory.messages.length;
  return (
    <>
      <div
        data-test="thread-column"
        className="flex-1 overflow-y-auto scroll px-5 py-6 min-h-0"
      >
        <div className="max-w-[680px] mx-auto text-center text-[12px] text-[--color-ink-muted]">
          {priorCount === 0 ? (
            <p>Ask the first question to begin this session.</p>
          ) : (
            <p>
              {priorCount} prior message{priorCount === 1 ? "" : "s"} will
              appear when the next message is sent.
            </p>
          )}
        </div>
      </div>
      <div className="border-t border-[--color-line] px-5 py-3 shrink-0">
        <div className="max-w-[680px] mx-auto">
          <div className="flex items-center gap-2 border border-[--color-line-strong] rounded-lg bg-[--color-canvas] px-3 py-2 focus-within:ring-2 focus-within:ring-[--color-accent]/15 focus-within:border-[--color-accent]/40 transition">
            <input
              type="text"
              placeholder={
                closed
                  ? "This session is closed"
                  : "Ask about the codebase…"
              }
              disabled={closed}
              className="flex-1 outline-none text-[13.5px] bg-transparent placeholder:text-[--color-ink-faint] disabled:cursor-not-allowed"
            />
            <kbd className="mono text-[10px] text-[--color-ink-faint] border border-[--color-line] rounded px-1.5 py-0.5">
              ⌘ ↵
            </kbd>
            <button
              type="button"
              disabled={closed}
              aria-label="Send"
              className="w-7 h-7 grid place-items-center rounded-md bg-[--color-ink] text-white hover:bg-[--color-ink]/90 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
            >
              <svg
                width={13}
                height={13}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden
              >
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
          </div>
          <p className="mono text-[10px] text-[--color-ink-faint] mt-2 text-center">
            Streaming over AG-UI · transport: SSE · cursor restores on reconnect
          </p>
        </div>
      </div>
    </>
  );
}
