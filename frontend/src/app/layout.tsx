import type { Metadata } from "next";
import { CopilotKit } from "@copilotkit/react-core";
import "@copilotkit/react-ui/styles.css";
import "./globals.css";

export const metadata: Metadata = {
  title: "kloc-agent",
  description: "Analyst chat over kloc-intelligence",
};

const AGENT_NAME =
  process.env.NEXT_PUBLIC_COPILOTKIT_AGENT_NAME ??
  process.env.COPILOTKIT_AGENT_NAME ??
  "kloc_agent";

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <CopilotKit runtimeUrl="/api/copilotkit" agent={AGENT_NAME}>
          {children}
        </CopilotKit>
      </body>
    </html>
  );
}
