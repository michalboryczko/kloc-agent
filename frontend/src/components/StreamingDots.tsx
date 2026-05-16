import { cn } from "@/lib/utils";

/**
 * Three-dot pulse indicator. Each dot lags the previous by 150ms via the
 * .kloc-pulse-dot-{n} delay classes in globals.css, producing a soft wave.
 *
 * Mountable wherever a "the agent is thinking" cue is wanted; the
 * CopilotChat surface already shows its own native indicator so this is
 * available for non-Copilot surfaces (transcript view, status strip).
 */
export function StreamingDots({
  className,
  label = "Streaming",
}: {
  className?: string;
  label?: string;
}) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn("inline-flex items-center gap-1", className)}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 rounded-full bg-[var(--text-mute)] animate-kloc-pulse-dot"
      />
      <span
        aria-hidden
        className="h-1.5 w-1.5 rounded-full bg-[var(--text-mute)] animate-kloc-pulse-dot kloc-pulse-dot-2"
      />
      <span
        aria-hidden
        className="h-1.5 w-1.5 rounded-full bg-[var(--text-mute)] animate-kloc-pulse-dot kloc-pulse-dot-3"
      />
    </span>
  );
}
