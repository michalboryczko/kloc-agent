import * as React from "react";
import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={cn(
          "flex min-h-[80px] w-full rounded-[10px] border border-[var(--line-strong)] bg-[var(--bg-2)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-dim)] outline-none transition-colors duration-[120ms] ease-[var(--ease-out-snappy)] focus:bg-[var(--bg-elev)] focus:border-[var(--accent-line)] focus:ring-2 focus:ring-[var(--accent-soft)] disabled:opacity-50 disabled:cursor-not-allowed resize-y",
          className,
        )}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";
