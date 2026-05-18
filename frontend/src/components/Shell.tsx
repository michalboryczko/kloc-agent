import type { ReactNode } from "react";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="h-screen flex overflow-hidden bg-[--color-canvas-sunk] text-[--color-ink]">
      {children}
    </div>
  );
}
