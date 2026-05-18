export function EmptyState({ message }: { message: string }) {
  return (
    <div
      data-test="empty-state"
      className="px-2 py-6 text-center text-[12px] text-[var(--color-ink-muted)]"
    >
      {message}
    </div>
  );
}
