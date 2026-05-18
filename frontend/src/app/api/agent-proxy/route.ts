import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

export async function POST(req: NextRequest) {
  const body = await req.text();
  let sessionId: string | undefined;
  try {
    const parsed = JSON.parse(body) as { sessionId?: string };
    sessionId = parsed?.sessionId;
  } catch {
    return new Response("Invalid JSON body", { status: 400 });
  }
  if (!sessionId) {
    return new Response("Missing sessionId", { status: 400 });
  }
  const upstream = await fetch(
    `${BACKEND_URL}/v1/sessions/${sessionId}/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body,
      cache: "no-store",
    },
  );
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
