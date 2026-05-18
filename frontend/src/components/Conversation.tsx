"use client";

import { useRunLoop } from "@/lib/runLoop";
import type { MessagesPage, SessionDetail } from "@/lib/types";
import { ConnectionBanner } from "./ConnectionBanner";
import { InputBar } from "./InputBar";
import { Thread } from "./Thread";

export function Conversation({
  initialDetail,
  initialHistory,
  autoFocusInput = false,
}: {
  initialDetail: SessionDetail;
  initialHistory: MessagesPage;
  autoFocusInput?: boolean;
}) {
  const { state, submit, retry } = useRunLoop({
    detail: initialDetail,
    initialMessages: initialHistory.messages,
    initialHasMore: initialHistory.has_more,
    initialOldestSeq: initialHistory.next_cursor,
  });

  const closed = state.detail?.closed_at !== null;
  const inFlight =
    state.connection === "connecting" ||
    state.connection === "live" ||
    state.connection === "replaying";

  return (
    <>
      <ConnectionBanner
        state={state.connection}
        errorMessage={state.errorMessage}
      />
      <Thread state={state} onRetry={retry} />
      <InputBar
        disabled={inFlight}
        closed={closed}
        onSubmit={submit}
        autoFocus={autoFocusInput}
      />
    </>
  );
}
