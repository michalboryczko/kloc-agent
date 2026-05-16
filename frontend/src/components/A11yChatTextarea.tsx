"use client";

import { useEffect, useRef, type ReactNode } from "react";

/**
 * Wraps a CopilotChat instance and labels its internal textarea.
 *
 * CopilotKit 1.56.5 renders its input as a bare `<textarea>` with no
 * `aria-label` or associated `<label>` (see
 * node_modules/@copilotkit/react-ui/dist/index.mjs:1788). Since
 * `CopilotChat` does not accept an `inputProps` slot, the only non-invasive
 * patch is a DOM-side fix: locate the textarea under our wrapper and set
 * `aria-label` on it. A MutationObserver keeps the label set across the
 * Input's internal remounts (auto-resize triggers DOM swaps in some
 * CopilotKit code paths).
 */
export function A11yChatTextarea({
  children,
  label = "Ask kloc analyst",
}: {
  children: ReactNode;
  label?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = ref.current;
    if (!root) return;

    const apply = () => {
      const ta = root.querySelector<HTMLTextAreaElement>("textarea");
      if (ta && ta.getAttribute("aria-label") !== label) {
        ta.setAttribute("aria-label", label);
      }
    };

    apply();
    const observer = new MutationObserver(apply);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [label]);

  return (
    <div ref={ref} className="contents">
      {children}
    </div>
  );
}
