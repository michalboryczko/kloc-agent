import type { ReactNode } from "react";

export function CodeChip({ children }: { children: ReactNode }) {
  return (
    <code
      data-test="code-chip"
      className="mono text-[11.5px] bg-[var(--color-canvas-sunk)] border border-[var(--color-line)] px-1.5 py-px rounded"
    >
      {children}
    </code>
  );
}
