import type { ToolCallView } from "@/lib/types";

const STYLES: Record<
  ToolCallView["state"],
  { wrap: string; icon: string; iconPath: string }
> = {
  running: {
    wrap: "border-[var(--color-line)] bg-[var(--color-canvas)]",
    icon: "text-[var(--color-warning)] spin",
    iconPath:
      "M21 12a9 9 0 1 1-6.219-8.56",
  },
  done: {
    wrap: "border-[var(--color-line)] bg-[var(--color-canvas)]",
    icon: "text-[var(--color-success)]",
    iconPath: "M5 13l4 4L19 7",
  },
  denied: {
    wrap: "border-[var(--color-danger-line)] bg-[var(--color-danger-bg)]",
    icon: "text-[var(--color-danger-ink)]",
    iconPath:
      "M4.93 4.93l14.14 14.14M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  },
};

export function ToolCallCard({ call }: { call: ToolCallView }) {
  const style = STYLES[call.state];
  const isDenied = call.state === "denied";
  return (
    <div
      data-test="tool-call"
      data-state={call.state}
      data-tool-id={call.id}
      className={`border rounded-md px-2.5 py-1.5 mb-1.5 ${style.wrap}`}
    >
      <div className="flex items-center gap-2 min-w-0">
        <svg
          width={13}
          height={13}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className={style.icon}
          aria-hidden
        >
          <path d={style.iconPath} />
        </svg>
        <span
          data-test="tool-name"
          className={`mono text-[12px] font-medium ${
            isDenied ? "text-[var(--color-danger-ink)]" : ""
          }`}
        >
          {call.name}
        </span>
        <span
          data-test="tool-args"
          className={`mono text-[11px] truncate ${
            isDenied ? "text-[var(--color-danger-ink)]/80" : "text-[var(--color-ink-muted)]"
          }`}
        >
          {summariseArgs(call.args)}
        </span>
        {isDenied ? (
          <span
            data-test="denied-label"
            className="ml-auto shrink-0 mono text-[10px] font-medium tracking-[0.06em] text-[var(--color-danger-ink)] border border-[var(--color-danger-line)] rounded px-1.5 py-0.5"
          >
            DENIED
          </span>
        ) : (
          <span
            data-test="tool-meta"
            className="ml-auto shrink-0 text-[11px] text-[var(--color-ink-muted)]"
          >
            {call.meta ?? ""}
          </span>
        )}
      </div>
      {isDenied ? (
        <p
          data-test="tool-meta"
          data-denied-reason=""
          className="mt-1 mono text-[11px] text-[var(--color-danger-ink)]/80"
        >
          {call.result ?? call.meta ?? ""}
        </p>
      ) : null}
    </div>
  );
}

function summariseArgs(args: string): string {
  if (!args) return "";
  const trimmed = args.trim();
  if (trimmed.length <= 80) return trimmed;
  return `${trimmed.slice(0, 77)}…`;
}
