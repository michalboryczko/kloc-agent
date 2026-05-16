import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // NEXT_PUBLIC_BACKEND_URL is auto-inlined into the client bundle by Next.js
  // (any process.env.NEXT_PUBLIC_* read in client code is replaced at build).
  // Default fallback lives in src/lib/config.ts; no redundant env-block here.
  //
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
