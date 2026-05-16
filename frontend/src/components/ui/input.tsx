import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type = "text", ...props }, ref) => {
    return (
      <input
        ref={ref}
        type={type}
        className={cn(
          "flex h-9 w-full rounded-md border border-[var(--line-strong)] bg-[var(--bg-2)] px-3 text-sm text-[var(--text)] placeholder:text-[var(--text-dim)] outline-none transition-colors duration-[120ms] ease-[var(--ease-out-snappy)] focus:bg-[var(--bg-elev)] focus:border-[var(--accent-line)] focus:ring-2 focus:ring-[var(--accent-soft)] disabled:opacity-50 disabled:cursor-not-allowed",
          className,
        )}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";
