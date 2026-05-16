import type { Metadata } from "next";
import { Geist, Instrument_Serif, JetBrains_Mono } from "next/font/google";
import "@copilotkit/react-ui/styles.css";
import "./globals.css";

const instrumentSerif = Instrument_Serif({
  weight: "400",
  style: ["normal", "italic"],
  subsets: ["latin"],
  display: "swap",
  variable: "--serif",
});

const geist = Geist({
  subsets: ["latin"],
  display: "swap",
  variable: "--sans",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--mono",
});

export const metadata: Metadata = {
  title: "kloc agent",
  description:
    "Analyst chat over an indexed codebase. Live agent reasoning, MCP tool calls, persistent audit log.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const bodyClass = `${instrumentSerif.variable} ${geist.variable} ${jetbrainsMono.variable}`;
  return (
    <html lang="en">
      <body className={bodyClass} suppressHydrationWarning>
        {children}
      </body>
    </html>
  );
}
