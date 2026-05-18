import type { ArtifactView } from "@/lib/types";
import { artifactDownloadUrl } from "@/lib/api";

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ArtifactChip({ artifact }: { artifact: ArtifactView }) {
  return (
    <a
      data-test="artifact-chip"
      data-artifact-id={artifact.id}
      href={artifactDownloadUrl(artifact.id)}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-md border border-[--color-line] bg-[--color-canvas-rail] hover:bg-[--color-canvas] transition-colors no-underline text-[--color-ink]"
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
        className="text-[--color-ink-muted]"
        aria-hidden
      >
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <path d="M14 2v6h6" />
      </svg>
      <span
        data-test="artifact-filename"
        className="mono text-[12px]"
      >
        {artifact.filename}
      </span>
      <span className="mono text-[10.5px] text-[--color-ink-muted]">
        {formatSize(artifact.size_bytes)}
      </span>
      <svg
        width={13}
        height={13}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-[--color-ink-muted] ml-1"
        aria-hidden
      >
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" />
      </svg>
    </a>
  );
}
