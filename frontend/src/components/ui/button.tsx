import * as React from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "default" | "ghost" | "danger";
type Size = "sm" | "md";

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-gradient-to-b from-[var(--accent-bright)] to-[var(--accent)] text-[#1a1208] font-semibold hover:from-[#ffc658] hover:to-[var(--accent-bright)] shadow-[0_1px_0_rgba(255,255,255,0.18)_inset,0_6px_18px_rgba(245,165,36,0.18)] focus-visible:ring-[var(--accent)]",
  default:
    "bg-[var(--bg-2)] border border-[var(--line-strong)] text-[var(--text)] hover:bg-[var(--bg-hover)] hover:border-[var(--line-bright)] focus-visible:ring-[var(--accent)]",
  ghost:
    "bg-transparent border border-[var(--line)] text-[var(--text-mute)] hover:text-[var(--text)] hover:border-[var(--line-bright)] focus-visible:ring-[var(--accent)]",
  danger:
    "bg-[var(--danger-soft)] border border-[var(--danger)] text-[var(--danger)] hover:bg-[var(--danger)] hover:text-[#1a0808] focus-visible:ring-[var(--danger)]",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-[30px] px-3 text-[12.5px]",
  md: "h-9 px-4 text-sm",
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", type = "button", ...props }, ref) => {
    return (
      <button
        ref={ref}
        type={type}
        className={cn(
          "inline-flex items-center justify-center gap-2 rounded-md whitespace-nowrap transition-colors duration-[120ms] ease-[var(--ease-out-snappy)] outline-none focus-visible:ring-2 focus-visible:ring-offset-0 disabled:opacity-50 disabled:cursor-not-allowed",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
