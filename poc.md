# Code Intelligence Research Agent — Project Brief

> Context prompt for a Claude Code agent that will scaffold the project. Describes what we are building, which architectural decisions are made, and which are still open. No implementation — the agent should research the referenced docs and write the code itself.

---

## What we are building?

A self-hosted research agent service. An analyst opens a web chat, asks a natural-language question about a code base (or any knowledge corpus exposed via MCP), and receives a synthesized, sourced answer.

We already operate an internal **code intelligence service** that solves the retrieval side. It exposes MCP, a CLI, and (soon) a REST API — semantic search over code, dependency graphs, enrichments. Today it is usable through Claude Code on an engineer's workstation. That works for one power user, not for analysts at scale.

This project delivers the missing layer: a hosted agent that consumes the intelligence service on behalf of an analyst, exposes a chat UI, keeps sessions alive across days, streams its reasoning live, and is controllable via lifecycle hooks (audit, policy).

Target user is an **analyst, not an engineer**. Opens a tab, asks a question, gets an answer, can come back tomorrow.

### Success criteria for PoC

An analyst can:

- Start a new session in the web UI.
- Send a prompt and watch the agent stream its reasoning and tool calls live.
- The agent uses at least one MCP tool from the intelligence service.
- The agent delegates to at least one sub-agent.
- The agent loads at least one skill via progressive disclosure.
- The agent produces a coherent, sourced answer.
- The session persists — closing and reopening resumes correctly.
- Every tool call and message lands in an audit log.

No auth for now. No multi-tenant. A single hardcoded analyst is fine.

---

## Abstract components

Three tiers plus persistence.

**Frontend.** Chat UI. Renders streamed agent events and tool calls. Owns no state; it is a view over the backend session.

**Backend.** Thin orchestration and persistence layer.

- API for the frontend (REST for session lifecycle, streaming for agent events).
- Runner management: spawn, monitor, evict per-session runners.
- Persistence: sessions, messages, audit log, artifact metadata.
- Hook webhook receiver: runners post lifecycle events here.

The backend does **not** run the agent loop, call the model, or call MCP directly.

**Agent runner.** Per-session ephemeral process. Runs the Strands agent loop. Loads skills (mounted read-only). Connects to the intelligence MCP. Streams events to the backend. Posts hook events to the backend webhook. Ephemeral — durable state lives in the backend, not in the runner.

**Persistence.** A relational store for structured data (sessions, messages, audit, artifact metadata) and an object store for artifact files.

### Data flow

User prompt → backend persists it → forwards to runner → agent loop produces events → events stream runner → backend → frontend in real time. Tool calls also fire hooks that POST to the backend webhook for audit. On finish, the final message and artifacts are persisted. If the session goes idle, the runner is evicted; on resume, a fresh runner is spawned and state is hydrated from the backend.

---

## Tech stack

### Confirmed

**Agent framework: [Strands Agents](https://strandsagents.com/).** Python SDK, Apache 2.0, model-agnostic, MCP-native, hooks, OpenTelemetry built in, multi-agent patterns. Chosen over Claude Agent SDK to avoid Node-in-image and keep a path to other models.

**Frontend protocol: [AG-UI](https://docs.ag-ui.com/) via [CopilotKit](https://www.copilotkit.ai/).** Native Strands integration. Chat, tool-based generative UI, shared state, frontend actions. Starting scaffold: `npx copilotkit create -f aws-strands-py`.

**Tool protocol: [MCP](https://modelcontextprotocol.io/).** Our intelligence service already speaks it.

**Skills: [Anthropic Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview) open spec.** Markdown with YAML frontmatter, progressive disclosure. Loaded in Strands via the [agentskills plugin](https://github.com/aws-samples/sample-strands-agents-agentskills).

**Backend: Python + [FastAPI](https://fastapi.tiangolo.com/) (asyncio).**

**Frontend: TypeScript + [Next.js](https://nextjs.org/) (App Router) + CopilotKit.**

**Package management: [uv](https://github.com/astral-sh/uv) for Python, [pnpm](https://pnpm.io/) for TypeScript.**

### To choose

**Runner isolation.** Subprocess (zero-overhead PoC), Docker per session (self-hosted production), or [AWS Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) (managed, microVM isolation, AG-UI deployment supported). Sits behind a single runner-backend interface so it can be swapped.

**Observability.** [Langfuse](https://langfuse.com/) (LLM-debugging UI) or OpenTelemetry to whatever (Strands emits OTel natively, so this is a config swap).

**Hook delivery.** In-process Python callbacks for trivial things (logging, light enrichment); HTTP webhooks to the backend for anything touching persistence or policy. Centralizes policy regardless of where runners live.

---

## References

**Strands.**
- Docs: https://strandsagents.com/
- Python SDK: https://github.com/strands-agents/sdk-python
- Samples: https://github.com/strands-agents/samples
- Session management: https://strandsagents.com/docs/user-guide/concepts/agents/session-management/
- Observability: https://strandsagents.com/docs/user-guide/observability-evaluation/observability/

**AG-UI / CopilotKit.**
- Strands × AG-UI integration: https://strandsagents.com/docs/community/integrations/ag-ui/
- AG-UI protocol: https://docs.ag-ui.com/
- AG-UI repo: https://github.com/ag-ui-protocol/ag-ui
- CopilotKit Strands docs: https://docs.copilotkit.ai/aws-strands/agentic-chat-ui
- AG-UI Dojo (playground): https://dojo.ag-ui.com

**MCP.**
- Spec: https://modelcontextprotocol.io/
- Strands MCP usage: https://strandsagents.com/docs/user-guide/concepts/tools/mcp-tools/

**Agent Skills.**
- Spec: https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview
- Strands plugin: https://github.com/aws-samples/sample-strands-agents-agentskills

**Reference projects to read before scaffolding similar parts.**
- Closest 1:1 to our use case (Strands + MCP + multi-agent + chat UI): https://github.com/aws-samples/sample-fraud-investigation-assistance-using-aws-bedrock-strandsagents-mcp
- End-to-end reference with AG-UI on AgentCore: https://github.com/aws-samples/sample-strands-agent-with-agentcore
- Clean FastAPI + React layout (file-per-agent, file-per-route): https://github.com/aws-samples/sample-AIOPS-agent-bedrock-strandsagents
- Official samples (Agentic RAG, evaluation): https://github.com/strands-agents/samples

---

## Operating principles for the implementing agent

- Read the referenced docs and reference projects before writing code. Most patterns already exist in `aws-samples/*` or `strands-agents/samples`.
- Where "To choose" lists alternatives, introduce an interface and a default implementation. Don't hardcode. Ask before committing.
- One concern per file.
- Persist before streaming. Every user message lands in the store before being forwarded to the runner. Assistant messages persist incrementally as they stream.
- Hooks are the policy layer. Anything that looks like "should this be allowed?" goes through a hook, not into the agent's tools or prompt.
- Skills over prompt engineering. Reusable behavior becomes a skill, not a paragraph in the system prompt.