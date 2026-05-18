export function RailFooter({
  repo,
  meta,
}: {
  repo: string;
  meta?: string;
}) {
  return (
    <div className="px-3 py-2.5 border-t border-[--color-line] bg-[--color-canvas-rail]">
      <div className="flex items-center gap-1.5 text-[--color-ink-muted]">
        <svg
          width={12}
          height={12}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M12 7c4.97 0 9-1.34 9-3s-4.03-3-9-3-9 1.34-9 3 4.03 3 9 3Z" />
          <path d="M3 4v6c0 1.66 4.03 3 9 3s9-1.34 9-3V4" />
          <path d="M3 10v6c0 1.66 4.03 3 9 3s9-1.34 9-3v-6" />
          <path d="M3 16v4c0 1.66 4.03 3 9 3s9-1.34 9-3v-4" />
        </svg>
        <span className="mono text-[11px] truncate">{repo}</span>
      </div>
      {meta && (
        <p className="mono text-[10px] text-[--color-ink-faint] pl-[18px] mt-0.5">
          {meta}
        </p>
      )}
    </div>
  );
}
