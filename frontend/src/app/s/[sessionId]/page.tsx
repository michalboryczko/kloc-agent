import { notFound } from "next/navigation";
import { Shell } from "@/components/Shell";
import { Sidebar } from "@/components/Sidebar";
import { ConversationHeader } from "@/components/ConversationHeader";
import { ErrorBanner } from "@/components/ErrorBanner";
import { getSession, listMessages } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { ConversationClient } from "./ConversationClient";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;

  let detail;
  let history;
  try {
    [detail, history] = await Promise.all([
      getSession(sessionId),
      listMessages(sessionId, { limit: 500 }),
    ]);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      notFound();
    }
    return (
      <Shell>
        <Sidebar activeId={sessionId} />
        <main className="flex-1 min-w-0 flex flex-col bg-[--color-canvas]">
          <header className="px-5 py-3 border-b border-[--color-line]" />
          <div className="flex-1 grid place-items-center p-6">
            <ErrorBanner
              message={
                e instanceof Error ? e.message : "Failed to load session"
              }
            />
          </div>
        </main>
      </Shell>
    );
  }

  return (
    <Shell>
      <Sidebar activeId={sessionId} />
      <main className="flex-1 min-w-0 flex flex-col bg-[--color-canvas]">
        <ConversationHeader
          title={detail.title}
          sessionId={detail.id}
          messageCount={history.messages.length}
          closed={detail.closed_at !== null}
        />
        <ConversationClient initialDetail={detail} initialHistory={history} />
      </main>
    </Shell>
  );
}
