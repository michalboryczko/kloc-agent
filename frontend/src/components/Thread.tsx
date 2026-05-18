"use client";

import { useEffect, useRef } from "react";
import type { MessageView, SessionViewState } from "@/lib/types";
import { AnalystBubble } from "./AnalystBubble";
import { AssistantBubble } from "./AssistantBubble";

export function Thread({
  state,
  onRetry,
}: {
  state: SessionViewState;
  onRetry: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastMessageId = state.messages[state.messages.length - 1]?.id ?? null;
  const lastContent =
    state.messages[state.messages.length - 1]?.content.length ?? 0;

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lastMessageId, lastContent, state.messages.length]);

  return (
    <div
      ref={scrollRef}
      data-test="thread-column"
      data-message-count={state.messages.length}
      className="flex-1 overflow-y-auto scroll px-5 py-6 min-h-0"
    >
      <div className="max-w-[680px] mx-auto">
        {state.messages.length === 0 ? (
          <p
            data-test="thread-empty"
            className="text-center text-[12px] text-[var(--color-ink-muted)]"
          >
            Ask the first question to begin this session.
          </p>
        ) : (
          state.messages.map((m, i) => renderMessage(m, i, state, onRetry))
        )}
      </div>
    </div>
  );
}

function renderMessage(
  m: MessageView,
  index: number,
  state: SessionViewState,
  onRetry: () => void,
) {
  if (m.role === "user") {
    return <AnalystBubble key={m.id} message={m} />;
  }
  if (m.role === "assistant") {
    const isLast = index === state.messages.length - 1;
    const showCaret =
      isLast && !m.finalized && state.connection === "live";
    const showRetry =
      isLast && state.connection === "error" && state.errorMessage !== null;
    return (
      <AssistantBubble
        key={m.id}
        message={m}
        showCaret={showCaret}
        showRetry={showRetry}
        onRetry={onRetry}
      />
    );
  }
  return null;
}
