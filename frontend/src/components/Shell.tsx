import type { ReactNode } from "react";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="h-screen flex overflow-hidden bg-[var(--color-canvas-sunk)] text-[var(--color-ink)]">
      {children}
    </div>
  );
}
