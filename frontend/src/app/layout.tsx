import type { Metadata } from "next";
import "./globals.css";
import { geist, jetbrainsMono } from "@/styles/fonts";
import { themeBootstrapScript } from "@/lib/theme";

export const metadata: Metadata = {
  title: "kloc analyst",
  description: "Single-analyst agent for an indexed codebase",
  icons: { icon: "/favicon.svg" },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html
      lang="en"
      className={`${geist.variable} ${jetbrainsMono.variable}`}
      suppressHydrationWarning
    >
      <head>
        <script
          dangerouslySetInnerHTML={{ __html: themeBootstrapScript }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
