import { cn } from "@/lib/utils";

type Status = "inProgress" | "executing" | "complete";

function looksLikeDenial(result: unknown): boolean {
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
  const badge = denied ? "denied" : status;

  return (
    <div
      className={cn(
        "my-2 rounded-lg border border-l-[2px] bg-[var(--bg-2)] px-3 py-2.5 text-[13px] text-[var(--text)]",
        denied
          ? "border-[var(--danger)]/40 border-l-[var(--danger)] bg-[var(--danger-soft)]"
          : "border-[var(--line)] border-l-[var(--accent)]",
      )}
    >
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <strong className="font-mono text-[13px] text-[var(--text)] truncate">
          {name}
        </strong>
        <span
          className={cn(
            "inline-flex h-5 items-center rounded-full border px-2 font-mono text-[10.5px] uppercase tracking-[0.14em]",
            denied
              ? "border-[var(--danger)]/40 bg-[var(--danger-soft)] text-[var(--danger)]"
              : "border-[var(--line-strong)] bg-[var(--bg-2)] text-[var(--text-mute)]",
          )}
        >
          {badge}
        </span>
      </div>

      {Object.keys(args).length > 0 && (
        <details className="group mt-2 text-[var(--text-mute)]">
          <summary className="flex cursor-pointer select-none items-center gap-1.5 text-[12.5px] hover:text-[var(--text)]">
            <span className="inline-block w-2 transition-transform duration-[120ms] ease-[var(--ease-out-snappy)] group-open:rotate-90">
              ▸
            </span>
            arguments
          </summary>
          <pre className="mt-1.5 overflow-x-auto rounded p-2 font-mono text-[12px] text-[var(--text)] bg-[var(--bg-0)] border border-[var(--line)]">
            {JSON.stringify(args, null, 2)}
          </pre>
        </details>
      )}

      {isComplete && result !== undefined && (
        <details open className="group mt-2 text-[var(--text-mute)]">
          <summary className="flex cursor-pointer select-none items-center gap-1.5 text-[12.5px] hover:text-[var(--text)]">
            <span className="inline-block w-2 transition-transform duration-[120ms] ease-[var(--ease-out-snappy)] group-open:rotate-90">
              ▸
            </span>
            result
          </summary>
          <pre className="mt-1.5 overflow-x-auto rounded p-2 font-mono text-[12px] text-[var(--text)] bg-[var(--bg-0)] border border-[var(--line)]">
            {typeof result === "string"
              ? result
              : JSON.stringify(result, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
