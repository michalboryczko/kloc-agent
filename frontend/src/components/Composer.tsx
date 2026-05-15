"use client";

// Composer — a plain controlled textarea for pages that build a chat shell
// outside CopilotSidebar/CopilotChat. The default path (page.tsx) does NOT
// use this; it's kept as a low-overhead primitive for the composability the
// file-ownership table calls out. (`@copilotkit/react-textarea`'s
// `CopilotTextarea` ships in its own package; we don't pull that just for a
// non-critical helper.)

export function Composer({
  value,
  onChange,
  onSubmit,
  placeholder = "Ask kloc…",
}: {
  value: string;
  onChange: (next: string) => void;
  onSubmit?: () => void;
  placeholder?: string;
}) {
  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (onSubmit && e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          onSubmit();
        }
      }}
      placeholder={placeholder}
      rows={2}
      style={{
        width: "100%",
        minHeight: 48,
        padding: "10px 12px",
        borderRadius: 8,
        border: "1px solid rgba(120, 120, 120, 0.3)",
        fontSize: 14,
        resize: "vertical",
        fontFamily: "inherit",
      }}
    />
  );
}
