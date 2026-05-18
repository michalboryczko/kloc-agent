import { Shell } from "@/components/Shell";
import { Sidebar } from "@/components/Sidebar";
import { ThemeToggle } from "@/components/ThemeToggle";

export default function LandingPage() {
  return (
    <Shell>
      <Sidebar />
      <main className="flex-1 min-w-0 flex flex-col bg-[--color-canvas]">
        <header className="flex items-center justify-end px-5 py-3 border-b border-[--color-line] shrink-0">
          <ThemeToggle />
        </header>
        <div className="flex-1 grid place-items-center px-6">
          <div className="text-center max-w-[420px]">
            <div className="w-10 h-10 mx-auto rounded-[8px] bg-[--color-chip-bg] grid place-items-center mb-4">
              <svg
                width={18}
                height={18}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                className="text-[--color-chip-fg]"
                aria-hidden
              >
                <path d="M12 2l2.4 6.6L21 11l-6.6 2.4L12 20l-2.4-6.6L3 11l6.6-2.4L12 2Z" />
              </svg>
            </div>
            <h1 className="text-[18px] font-medium tracking-tight mb-2">
              kloc analyst
            </h1>
            <p className="text-[13px] text-[--color-ink-muted] leading-relaxed">
              Pick a session from the rail to resume, or start a new one to ask
              about the indexed codebase.
            </p>
          </div>
        </div>
      </main>
    </Shell>
  );
}
