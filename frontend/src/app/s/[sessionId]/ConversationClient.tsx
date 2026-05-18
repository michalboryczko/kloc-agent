"use client";

import type { MessagesPage, SessionDetail } from "@/lib/types";
import { Conversation } from "@/components/Conversation";

export function ConversationClient({
  initialDetail,
  initialHistory,
}: {
  initialDetail: SessionDetail;
  initialHistory: MessagesPage;
}) {
  return (
    <Conversation
      initialDetail={initialDetail}
      initialHistory={initialHistory}
      autoFocusInput={initialHistory.messages.length === 0}
    />
  );
}
