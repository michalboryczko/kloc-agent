import type { ReactNode } from "react";

export function CodeChip({ children }: { children: ReactNode }) {
  return (
    <code
      data-test="code-chip"
      className="mono text-[11.5px] bg-[--color-canvas-sunk] border border-[--color-line] px-1.5 py-px rounded"
    >
      {children}
    </code>
  );
}
