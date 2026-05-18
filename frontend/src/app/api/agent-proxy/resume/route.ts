import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  const sessionId = url.searchParams.get("session_id");
  const runId = url.searchParams.get("run_id");
  const lastEventId = url.searchParams.get("last_event_id");
  if (!sessionId || !runId) {
    return new Response("Missing session_id or run_id", { status: 400 });
  }
  const upstreamUrl = new URL(
    `${BACKEND_URL}/v1/sessions/${sessionId}/stream`,
  );
  upstreamUrl.searchParams.set("run_id", runId);
  if (lastEventId !== null) {
    upstreamUrl.searchParams.set("last_event_id", lastEventId);
  }
  const upstream = await fetch(upstreamUrl, {
    method: "GET",
    headers: { Accept: "text/event-stream" },
    cache: "no-store",
  });
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type":
        upstream.headers.get("content-type") ?? "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
