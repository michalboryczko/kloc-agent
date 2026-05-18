export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div
      data-test="error-banner"
      role="alert"
      className="border border-[--color-danger-line] bg-[--color-danger-bg] text-[--color-danger-ink] rounded-md px-3 py-2 text-[12px] flex items-center gap-3"
    >
      <span className="flex-1">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mono text-[10.5px] tracking-[0.06em] uppercase border border-[--color-danger-line] rounded px-1.5 py-0.5 hover:bg-[--color-danger-line]/40"
        >
          Retry
        </button>
      )}
    </div>
  );
}
