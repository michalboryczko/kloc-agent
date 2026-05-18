"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createSession } from "@/lib/api";
import { ErrorBanner } from "./ErrorBanner";

export function NewSessionButton() {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setError(null);
    setPending(true);
    try {
      const { session_id } = await createSession();
      router.push(`/s/${session_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create session");
      setPending(false);
    }
  }

  return (
    <div className="space-y-1">
      <button
        type="button"
        data-test="new-session-btn"
        onClick={handleClick}
        disabled={pending}
        className="w-full flex items-center justify-center gap-1.5 text-[13px] font-medium py-1.5 rounded-md border border-[var(--color-line-strong)] bg-[var(--color-canvas)] hover:bg-[var(--color-canvas-sunk)] transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
      >
        <svg
          width={14}
          height={14}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M5 12h14M12 5v14" />
        </svg>
        {pending ? "Creating…" : "New session"}
      </button>
      {error && <ErrorBanner message={error} />}
    </div>
  );
}
