import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000",
  },
  // @copilotkit/runtime ships pino (logger), which transitively pulls in
  // thread-stream and its test files (`why-is-node-running`,
  // `tap-mocha-reporter`) that Next.js' bundler resolves and fails on.
  // Treating these as external server packages keeps them out of the
  // webpack graph; Node resolves them at runtime from node_modules.
  serverExternalPackages: [
    "@copilotkit/runtime",
    "pino",
    "pino-pretty",
    "thread-stream",
  ],
};

export default nextConfig;
