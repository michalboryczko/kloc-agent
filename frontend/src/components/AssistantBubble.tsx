import type { MessageView } from "@/lib/types";
import { ArtifactChip } from "./ArtifactChip";
import { BlinkingCaret } from "./BlinkingCaret";
import { ToolCallCard } from "./ToolCallCard";

export function AssistantBubble({
  message,
  showCaret,
  showRetry,
  onRetry,
}: {
  message: MessageView;
  showCaret: boolean;
  showRetry: boolean;
  onRetry: () => void;
}) {
  return (
    <div
      data-test="message"
      data-role="assistant"
      data-message-id={message.id}
      data-seq={message.seq ?? undefined}
      className="flex gap-3 mb-7"
    >
      <div className="w-7 h-7 rounded-md bg-[var(--color-chip-bg)] grid place-items-center shrink-0">
        <svg
          width={13}
          height={13}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="text-[var(--color-chip-fg)]"
          aria-hidden
        >
          <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2Z" />
        </svg>
      </div>
      <div data-test="assistant-bubble" className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 mb-2">
          <span className="text-[12px] font-medium">kloc analyst</span>
        </div>
        {message.toolCalls.length > 0 ? (
          <div>
            {message.toolCalls.map((call) => (
              <ToolCallCard key={call.id} call={call} />
            ))}
          </div>
        ) : null}
        {message.content || showCaret ? (
          <p className="text-[14px] leading-[1.65] mt-3 mb-4 whitespace-pre-wrap break-words">
            {message.content}
            {showCaret ? <BlinkingCaret /> : null}
          </p>
        ) : null}
        {message.artifacts.length > 0 ? (
          <div className="flex flex-wrap gap-2 mt-2">
            {message.artifacts.map((a) => (
              <ArtifactChip key={a.id} artifact={a} />
            ))}
          </div>
        ) : null}
        {showRetry ? (
          <div className="mt-2">
            <button
              type="button"
              data-test="retry-message"
              onClick={onRetry}
              className="mono text-[11px] px-2 py-1 rounded border border-[var(--color-danger-line)] bg-[var(--color-danger-bg)] text-[var(--color-danger-ink)] hover:opacity-90"
            >
              retry
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
