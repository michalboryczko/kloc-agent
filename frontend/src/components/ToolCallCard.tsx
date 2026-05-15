// Generative-UI tool-call card.
//
// Rendered by `useCopilotAction({name:"*"})` for every server-side tool call
// (`kloc_*` MCP tools, `file_read` from skills, sub-agent delegations, etc.).
// Receives the tool name, the streamed arguments, the call status, and (once
// available) the result. Per plan F4: one generic card, no per-tool
// customisations. AC19 surface: when the hook denies a tool, the runner
// emits TOOL_CALL_RESULT with `result` set to the denial reason — the card
// switches to a red theme to make the denial visually distinct.

type Status = "inProgress" | "executing" | "complete";

function looksLikeDenial(result: unknown): boolean {
  // Strands sets `event.cancel_tool = reason` on deny; the AG-UI
  // TOOL_CALL_RESULT then carries the reason string. Convention is
  // `policy_<reason>` or `test-deny:<tool>` per the C2 fixture in plan
  // Contract C. We light up the card on any of those prefixes plus a
  // generic "denied" / "deadline_exceeded" fallback.
  if (typeof result !== "string") return false;
  const s = result.toLowerCase();
  return (
    s.startsWith("policy_") ||
    s.startsWith("test-deny:") ||
    s.includes("denied") ||
    s.includes("deadline_exceeded")
  );
}

export function ToolCallCard({
  name,
  args,
  status,
  result,
}: {
  name: string;
  args: Record<string, unknown>;
  status: Status | string;
  result?: unknown;
}) {
  const isComplete = status === "complete";
  const denied = isComplete && looksLikeDenial(result);
  const palette = denied
    ? {
        border: "rgba(220, 70, 70, 0.55)",
        background: "rgba(220, 70, 70, 0.08)",
        badge: "denied",
      }
    : {
        border: "rgba(120, 120, 120, 0.25)",
        background: "rgba(120, 120, 120, 0.06)",
        badge: status,
      };
  return (
    <div
      style={{
        border: `1px solid ${palette.border}`,
        borderRadius: 8,
        padding: "10px 12px",
        margin: "8px 0",
        fontSize: 13,
        background: palette.background,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          marginBottom: 6,
        }}
      >
        <strong style={{ fontFamily: "ui-monospace, monospace" }}>{name}</strong>
        <span style={{ opacity: 0.65, fontSize: 12 }}>{palette.badge}</span>
      </div>
      {Object.keys(args).length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", opacity: 0.8 }}>arguments</summary>
          <pre
            style={{
              margin: "6px 0 0",
              padding: 8,
              background: "rgba(0,0,0,0.08)",
              borderRadius: 4,
              fontSize: 12,
              overflowX: "auto",
            }}
          >
            {JSON.stringify(args, null, 2)}
          </pre>
        </details>
      )}
      {isComplete && result !== undefined && (
        <details open>
          <summary style={{ cursor: "pointer", opacity: 0.8 }}>result</summary>
          <pre
            style={{
              margin: "6px 0 0",
              padding: 8,
              background: "rgba(0,0,0,0.08)",
              borderRadius: 4,
              fontSize: 12,
              overflowX: "auto",
            }}
          >
            {typeof result === "string"
              ? result
              : JSON.stringify(result, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
