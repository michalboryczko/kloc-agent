export function BlinkingCaret() {
  return (
    <span
      data-test="blinking-caret"
      aria-hidden
      className="blink text-[var(--color-ink-muted)] ml-0.5"
    >
      ▍
    </span>
  );
}
