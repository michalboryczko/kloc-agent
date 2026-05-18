import type { MessageView } from "@/lib/types";

export function AnalystBubble({ message }: { message: MessageView }) {
  return (
    <div
      data-test="message"
      data-role="analyst"
      data-message-id={message.id}
      data-seq={message.seq ?? undefined}
      className="flex gap-3 mb-7"
    >
      <div className="w-7 h-7 rounded-full bg-[--color-accent]/10 text-[--color-accent] grid place-items-center shrink-0 mono text-[10px] font-medium">
        MR
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-[12px] font-medium">Analyst</span>
        </div>
        <p className="text-[14px] leading-relaxed whitespace-pre-wrap break-words">
          {message.content}
        </p>
      </div>
    </div>
  );
}
