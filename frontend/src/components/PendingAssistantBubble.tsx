export function PendingAssistantBubble() {
  return (
    <div
      data-test="assistant-pending"
      data-role="assistant"
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
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 mb-2">
          <span className="text-[12px] font-medium">kloc analyst</span>
        </div>
        <div
          role="status"
          aria-label="kloc analyst is thinking"
          className="flex items-center gap-1 mt-3 mb-4 text-[var(--color-ink-muted)]"
        >
          <span
            className="pulse-dot inline-block w-1.5 h-1.5 rounded-full bg-current"
            style={{ animationDelay: "0ms" }}
          />
          <span
            className="pulse-dot inline-block w-1.5 h-1.5 rounded-full bg-current"
            style={{ animationDelay: "200ms" }}
          />
          <span
            className="pulse-dot inline-block w-1.5 h-1.5 rounded-full bg-current"
            style={{ animationDelay: "400ms" }}
          />
        </div>
      </div>
    </div>
  );
}
