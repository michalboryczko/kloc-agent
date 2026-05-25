"use client";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const SAFE_URL = /^(https?:|mailto:|tel:|#|\/|\.)/i;
const SAFE_IMAGE = /^(https?:|data:image\/)/i;

function urlTransform(url: string) {
  return SAFE_URL.test(url) ? url : "#";
}
function imageTransform(url: string) {
  return SAFE_IMAGE.test(url) ? url : "";
}

const components: Components = {
  a: ({ href, children, ...rest }) => (
    <a
      {...rest}
      href={href}
      target="_blank"
      rel="noopener noreferrer nofollow"
      className="underline text-[var(--color-accent)] break-words"
    >
      {children}
    </a>
  ),
  // react-markdown v9 removed `inline` from the prop signature; the only
  // marker left is whether className carries a `language-*` token, which is
  // injected only for fenced blocks.
  code: ({ className, children, ...rest }) => {
    const isInline = !className || !className.startsWith("language-");
    return isInline ? (
      <code
        {...rest}
        className="px-1 py-0.5 mx-0.5 rounded bg-[var(--color-chip-bg)] text-[var(--color-chip-fg)] mono text-[12.5px]"
      >
        {children}
      </code>
    ) : (
      <code {...rest} className={`${className ?? ""} mono text-[12.5px] leading-[1.55]`}>
        {children}
      </code>
    );
  },
  pre: ({ children, ...rest }) => (
    <pre {...rest} className="my-3 p-3 rounded bg-[var(--color-chip-bg)] overflow-x-auto">
      {children}
    </pre>
  ),
  p: ({ children, ...rest }) => (
    <p {...rest} className="text-[14px] leading-[1.65] mt-3 mb-3 break-words">
      {children}
    </p>
  ),
  ul: ({ children, ...rest }) => (
    <ul {...rest} className="list-disc pl-5 my-2 space-y-1 text-[14px] leading-[1.65]">
      {children}
    </ul>
  ),
  ol: ({ children, ...rest }) => (
    <ol {...rest} className="list-decimal pl-5 my-2 space-y-1 text-[14px] leading-[1.65]">
      {children}
    </ol>
  ),
  h1: ({ children, ...rest }) => (
    <h1 {...rest} className="text-[18px] font-semibold mt-4 mb-2">{children}</h1>
  ),
  h2: ({ children, ...rest }) => (
    <h2 {...rest} className="text-[16px] font-semibold mt-4 mb-2">{children}</h2>
  ),
  h3: ({ children, ...rest }) => (
    <h3 {...rest} className="text-[14px] font-semibold mt-3 mb-1.5">{children}</h3>
  ),
  blockquote: ({ children, ...rest }) => (
    <blockquote
      {...rest}
      className="border-l-2 border-[var(--color-chip-bg)] pl-3 my-3 italic"
    >
      {children}
    </blockquote>
  ),
  table: ({ children, ...rest }) => (
    <div className="my-3 overflow-x-auto">
      <table {...rest} className="w-full text-[13px] border-collapse">{children}</table>
    </div>
  ),
  th: ({ children, ...rest }) => (
    <th {...rest} className="text-left font-medium border-b border-[var(--color-chip-bg)] px-2 py-1">
      {children}
    </th>
  ),
  td: ({ children, ...rest }) => (
    <td {...rest} className="border-b border-[var(--color-chip-bg)] px-2 py-1 align-top">{children}</td>
  ),
  hr: ({ ...rest }) => <hr {...rest} className="my-4 border-[var(--color-chip-bg)]" />,
  img: ({ src, alt, ...rest }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      {...rest}
      src={typeof src === "string" ? imageTransform(src) : ""}
      alt={alt ?? ""}
      loading="lazy"
      className="my-3 max-w-full rounded"
    />
  ),
};

export function MarkdownContent({ content }: { content: string }) {
  return (
    <div data-test="assistant-markdown" className="min-w-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={urlTransform}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
