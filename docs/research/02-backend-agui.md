# 02 — Backend & AG-UI streaming

Scope: the FastAPI orchestration layer, the wire protocol it streams over, and how it glues per-session Strands runners to a CopilotKit/Next.js frontend. Versions verified at time of writing: `ag-ui-protocol` **0.1.18** (Python SDK on the `ag-ui-protocol/ag-ui` repo, default branch `main`, last update 2026-05-13), `ag_ui_strands` **0.1.8** (`integrations/aws-strands/python` in the same repo, depends on `strands-agents>=1.15.0`), `@copilotkit/runtime` and `@copilotkit/react-*` **1.52.1**, `@ag-ui/client` **^0.0.42**, Next.js **16.0.8**. The AG-UI protocol itself does not publish a semver number; the only authoritative version surface is the Python package (`0.1.18`) and the TypeScript `@ag-ui/core` package.

Out of scope: persistence schema, MCP wiring inside the runner, skill loading, runner sandboxing, observability stack.

---

## 1. SSE vs WebSocket — recommendation: **SSE**

### What the AG-UI spec actually mandates

AG-UI "doesn't mandate how events are delivered" — the protocol explicitly lists SSE, WebSocket, webhooks, and a binary HTTP protocol as supported transports (`docs.ag-ui.com/concepts/architecture.md`). The Python `EventEncoder` ships with **SSE as the default and only encoder** (`sdks/python/ag_ui/encoder/encoder.py`):

```python
AGUI_MEDIA_TYPE = "application/vnd.ag-ui.event+proto"

class EventEncoder:
    def get_content_type(self) -> str:
        return "text/event-stream"

    def encode(self, event: BaseEvent) -> str:
        return self._encode_sse(event)

    def _encode_sse(self, event: BaseEvent) -> str:
        return f"data: {event.model_dump_json(by_alias=True, exclude_none=True)}\n\n"
```

The reference Strands integration (`ag_ui_strands.endpoint.add_strands_fastapi_endpoint`) hands the encoder a `Request` object so the client *could* negotiate via `Accept`, but in practice the encoder only knows SSE. So when we say "AG-UI on the wire", we mean **JSON-encoded events, one per `data:` SSE frame, framed by `\n\n`**.

### Trade-off matrix for our use case

| Concern | SSE | WebSocket |
|---|---|---|
| Server → client streaming | Native. One unidirectional pipe per HTTP response. | Native. Bidirectional. |
| Client → server messages (chat input) | Out-of-band — a separate `POST /sessions/{id}/messages`. | In-band — same socket. |
| Long-lived sessions (hours, days) | Fine. Reconnect via `Last-Event-ID` is standard. Browser does it automatically. | Fine but you own keepalive and reconnect logic. |
| Reconnection semantics | Browser auto-reconnects, the SSE protocol natively supports a resume cursor (`Last-Event-ID` header). | None built-in. Must implement application-level. |
| Multiplexing N streams per session | Multiple `EventSource` connections is fine; or multiplex on the server. | One socket can carry everything, but you invent framing. |
| Back-pressure | Just stop pulling from the async generator; uvicorn handles TCP back-pressure. | Need explicit `await ws.send` discipline + queue between agent and socket. |
| Auth | Cookies + `Authorization` header (with `EventSource` polyfill) or token in query. | Same problem, slightly worse — `WebSocket` API can't set headers in browsers. |
| Debugging | `curl -N http://.../stream` works. | Need `wscat` / browser devtools. |
| Proxy/CDN friendliness | Plain HTTP. Just disable response buffering. | Some intermediaries strip `Upgrade: websocket`. |
| Server reload during dev | New connection on reconnect; trivial. | Same, plus you have to clear in-memory socket registries. |

### Why SSE wins for `kloc-agent`

1. **The agent loop is server-driven.** The user types a message, then watches the agent stream events for ~30 s to 2 min. There is no high-frequency client→server chatter during a run — the few outbound things (cancel, tool approval, frontend tool result) are coarse and tolerate a separate `POST`.
2. **The AG-UI reference path is SSE.** `EventEncoder.encode` only emits SSE. Going WS would mean writing our own encoder *and* a custom CopilotKit client. CopilotKit's `HttpAgent` (used in the scaffold) **POSTs the `RunAgentInput`, then reads SSE off the response body**. Swapping that for WS would mean replacing the entire CopilotKit runtime adapter.
3. **`EventSource` reconnect maps perfectly to our resume story.** When the analyst comes back tomorrow, the browser reconnects the SSE stream automatically, replays a `MESSAGES_SNAPSHOT` (cheap, idempotent), and the runner picks up. We get the hard part of WS reconnection for free.
4. **Back-pressure is implicit.** A slow client just slows down the async generator's `yield`, which slows the Strands `stream_async()` consumer, which slows the model. No buffer to grow unboundedly. With WS we'd be pushing into a per-connection queue we have to size and shed from.

We keep one **WebSocket exception** open for a *single* future need: bidirectional voice / tool-approval-with-typing. Not on the PoC critical path.

### The FastAPI pattern we use

`ag_ui_strands.endpoint.add_strands_fastapi_endpoint` is exactly the shape we need, plus session lifecycle around it:

```python
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from ag_ui.core import RunAgentInput, RunErrorEvent, EventType
from ag_ui.encoder import EventEncoder

@app.post("/sessions/{session_id}/stream")
async def stream(session_id: str, input_data: RunAgentInput, request: Request):
    encoder = EventEncoder(accept=request.headers.get("accept"))

    async def event_generator():
        # 1. Persist the user message BEFORE forwarding to the runner
        await store.append_user_message(session_id, input_data)

        # 2. Get or spawn the runner, forward, multiplex its event stream
        runner = await runners.get_or_spawn(session_id)
        try:
            async for event in runner.run(input_data):  # AG-UI events
                # 3. Persist each event (incremental) and yield to the client
                await store.append_event(session_id, event)
                if await request.is_disconnected():
                    # Client gone; runner continues so resume gets the tail.
                    break
                try:
                    yield encoder.encode(event)
                except Exception as exc:
                    yield encoder.encode(RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message=f"Encoding error: {exc}",
                        code="ENCODING_ERROR",
                    ))
                    break
        finally:
            await runners.release(session_id)  # idle timer, not eviction

    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
```

Three things to note:

- `request.is_disconnected()` is FastAPI/Starlette's TCP-level disconnect check. Calling it inside the generator gives us a clean break when the analyst closes the tab.
- The generator yields **bytes the encoder produces**, which are valid SSE frames (`data: {...}\n\n`). We never construct SSE by hand.
- We **persist before yielding**. If the network drops mid-message, the store has the event; the client replays on reconnect.

CopilotKit's `HttpAgent` calls this with `Accept: text/event-stream` and parses each `data:` frame as JSON via the `@ag-ui/core` schema.

---

## 2. AG-UI event taxonomy (full enumeration)

Source: `sdks/python/ag_ui/core/events.py` in `ag-ui-protocol/ag-ui` on `main`. The Python enum `EventType` is the canonical list; the TypeScript SDK mirrors it. **33 event types** in 8 categories. All events inherit from `BaseEvent`:

```python
class BaseEvent(ConfiguredBaseModel):
    type: EventType           # discriminator
    timestamp: Optional[int]  # ms since epoch (optional)
    raw_event: Optional[Any]  # provider-native, opaque pass-through
```

Wire format: each event is `event.model_dump_json(by_alias=True, exclude_none=True)`. `ConfiguredBaseModel` enables `alias_generator=to_camel`, so Python `thread_id` serializes as JSON `threadId`. This matters for cross-language interop.

### 2.1 Run lifecycle (5)

| Event | When | Fields |
|---|---|---|
| `RUN_STARTED` | First event in any run | `threadId`, `runId`, optional `parentRunId`, optional `input: RunAgentInput` |
| `RUN_FINISHED` | Successful (or interrupt) completion | `threadId`, `runId`, optional `result`, optional `outcome: {type:"success"} \| {type:"interrupt", interrupts:[...]}` |
| `RUN_ERROR` | Unrecoverable failure | `message`, optional `code` |
| `STEP_STARTED` | Discrete sub-step opens (multi-agent node, retrieval phase, etc.) | `stepName` |
| `STEP_FINISHED` | Closes the matching step | `stepName` (must match) |

Sample wire JSON:

```json
{"type":"RUN_STARTED","threadId":"thr_8b","runId":"run_01HM","timestamp":1747200001000}
{"type":"STEP_STARTED","stepName":"agent:planner","timestamp":1747200001120}
{"type":"STEP_FINISHED","stepName":"agent:planner","timestamp":1747200002800}
{"type":"RUN_FINISHED","threadId":"thr_8b","runId":"run_01HM","outcome":{"type":"success"},"timestamp":1747200012333}
```

`RUN_ERROR` semantics: when emitted, no further events are valid for that `runId`. The Strands adapter uses `code="STRANDS_ERROR"` for runner exceptions and `code="ENCODING_ERROR"` for serialization failures.

### 2.2 Text message streaming (4)

| Event | When | Fields |
|---|---|---|
| `TEXT_MESSAGE_START` | New assistant (or system/user/developer) message begins | `messageId`, `role` (default `"assistant"`), optional `name` |
| `TEXT_MESSAGE_CONTENT` | One delta chunk; concatenate in order | `messageId`, `delta: str` (non-empty) |
| `TEXT_MESSAGE_END` | Streaming complete; finalize render | `messageId` |
| `TEXT_MESSAGE_CHUNK` | Convenience wrapper that may carry start+content+end | optional `messageId`, optional `role`, optional `delta`, optional `name` |

Sample wire JSON (token stream of "Hello world"):

```json
{"type":"TEXT_MESSAGE_START","messageId":"msg_a1","role":"assistant"}
{"type":"TEXT_MESSAGE_CONTENT","messageId":"msg_a1","delta":"Hello"}
{"type":"TEXT_MESSAGE_CONTENT","messageId":"msg_a1","delta":" world"}
{"type":"TEXT_MESSAGE_END","messageId":"msg_a1"}
```

Note `role` is constrained: `Literal["developer","system","assistant","user"]` — tool messages don't use this event family, they use `TOOL_CALL_RESULT`.

### 2.3 Tool call streaming (5)

| Event | When | Fields |
|---|---|---|
| `TOOL_CALL_START` | Model emits a tool invocation | `toolCallId`, `toolCallName`, optional `parentMessageId` |
| `TOOL_CALL_ARGS` | Argument JSON streams in fragments | `toolCallId`, `delta: str` |
| `TOOL_CALL_END` | Arguments fully delivered; tool may begin executing | `toolCallId` |
| `TOOL_CALL_RESULT` | Result of execution (one shot) | `messageId`, `toolCallId`, `content: str`, optional `role: "tool"` |
| `TOOL_CALL_CHUNK` | Convenience wrapper | optional `toolCallId` (req on first), optional `toolCallName` (req on first), optional `parentMessageId`, optional `delta` |

Sample wire JSON (`get_weather` call):

```json
{"type":"TOOL_CALL_START","toolCallId":"call_42","toolCallName":"get_weather","parentMessageId":"msg_a1"}
{"type":"TOOL_CALL_ARGS","toolCallId":"call_42","delta":"{\"location"}
{"type":"TOOL_CALL_ARGS","toolCallId":"call_42","delta":"\":\"SF\"}"}
{"type":"TOOL_CALL_END","toolCallId":"call_42"}
{"type":"TOOL_CALL_RESULT","messageId":"msg_b1","toolCallId":"call_42","content":"{\"temp\":22}","role":"tool"}
```

The Strands adapter emits `TOOL_CALL_RESULT` **without** the `role` field when the result came from a server-side tool, so CopilotKit doesn't duplicate a `tool` message in its history — it relies on the subsequent `MESSAGES_SNAPSHOT` for canonical history.

### 2.4 State (3)

| Event | When | Fields |
|---|---|---|
| `STATE_SNAPSHOT` | Set or reset the shared state object | `snapshot: any` (full object; replace, don't merge) |
| `STATE_DELTA` | Mutate state incrementally | `delta: list[JsonPatchOp]` — RFC 6902 ops `{op,path,value?,from?}` |
| `MESSAGES_SNAPSHOT` | Canonical conversation history (on resume / boundary) | `messages: List[Message]` |

Sample `STATE_DELTA`:

```json
{"type":"STATE_DELTA","delta":[
  {"op":"add","path":"/searchResults","value":[]},
  {"op":"replace","path":"/status","value":"running"}
]}
```

The Strands adapter emits `MESSAGES_SNAPSHOT` at four boundaries (per its `ARCHITECTURE.md`): after the initial `STATE_SNAPSHOT`, after every `TOOL_CALL_END`, after every `TOOL_CALL_RESULT`, and after every `TEXT_MESSAGE_END`. **Each snapshot carries the full thread state up to that point.** This is our primary on-the-wire crosscheck for persistence.

### 2.5 Activity (2) — frontend-only progress messages

| Event | When | Fields |
|---|---|---|
| `ACTIVITY_SNAPSHOT` | Full progress card (search-in-progress, plan tree, etc.) | `messageId`, `activityType`, `content: Any`, `replace: bool = True` |
| `ACTIVITY_DELTA` | Patch a previous snapshot | `messageId`, `activityType`, `patch: list[JsonPatchOp]` |

Activity messages never reach the model — they're pure UI. Useful when we want to render "loading 47 files…" without polluting message history.

### 2.6 Reasoning (7)

For agents that expose chain-of-thought (e.g. Claude extended thinking, DeepSeek reasoning). Strands adapter maps `stream_async` events with `reasoning=True` to these:

| Event | Fields |
|---|---|
| `REASONING_START` | `messageId` |
| `REASONING_MESSAGE_START` | `messageId`, `role: "reasoning"` |
| `REASONING_MESSAGE_CONTENT` | `messageId`, `delta` |
| `REASONING_MESSAGE_END` | `messageId` |
| `REASONING_MESSAGE_CHUNK` | optional `messageId`, optional `delta` |
| `REASONING_END` | `messageId` |
| `REASONING_ENCRYPTED_VALUE` | `subtype: "message"\|"tool-call"`, `entityId`, `encryptedValue` (base64) |

`REASONING_ENCRYPTED_VALUE` carries opaque encrypted thought (Anthropic redacted thinking), so we persist and replay it across turns without ever decoding it.

### 2.7 "Thinking" lightweight events (4)

Distinct from full reasoning streaming. Lightweight bracket events:

| Event | Fields |
|---|---|
| `THINKING_START` | optional `title` |
| `THINKING_END` | — |
| `THINKING_TEXT_MESSAGE_START` | — |
| `THINKING_TEXT_MESSAGE_CONTENT` | `delta` |
| `THINKING_TEXT_MESSAGE_END` | — |

These exist for backwards compatibility with earlier integrations; new code prefers the `REASONING_*` family.

### 2.8 Escape hatches (2)

| Event | Fields |
|---|---|
| `RAW` | `event: Any`, optional `source` — pass-through for native model events not yet mapped |
| `CUSTOM` | `name: str`, `value: Any` — protocol extension |

The Strands adapter uses `CUSTOM` for two things today: `CustomEvent(name="PredictState", ...)` for optimistic UI updates and `CustomEvent(name="MultiAgentHandoff", value={...})` when a Strands graph hands off between nodes.

### Total: 33 event types

```
TEXT_MESSAGE_START / CONTENT / END / CHUNK                                       (4)
THINKING_TEXT_MESSAGE_START / CONTENT / END                                      (3)
TOOL_CALL_START / ARGS / END / CHUNK / RESULT                                    (5)
THINKING_START / END                                                             (2)
STATE_SNAPSHOT / STATE_DELTA / MESSAGES_SNAPSHOT                                 (3)
ACTIVITY_SNAPSHOT / ACTIVITY_DELTA                                               (2)
RAW / CUSTOM                                                                     (2)
RUN_STARTED / RUN_FINISHED / RUN_ERROR / STEP_STARTED / STEP_FINISHED            (5)
REASONING_START / MESSAGE_START / CONTENT / END / CHUNK / END / ENCRYPTED_VALUE  (7)
```

(Reasoning count is 7 because `REASONING_START` and `REASONING_END` are separate from `REASONING_MESSAGE_END`.)

There is also a `MetaEvent` DRAFT (`docs.ag-ui.com/drafts/meta-events.md`) for side-band annotations that don't belong to a run, but it is not in the Python event union and we should not depend on it.

### `RunAgentInput` — the inbound shape

The client `POST` payload that triggers a run (`sdks/python/ag_ui/core/types.py`):

```python
class RunAgentInput(ConfiguredBaseModel):
    thread_id: str            # our session_id maps 1:1 here
    run_id: str               # client-generated UUID for this turn
    parent_run_id: Optional[str]
    state: Any                # current shared state (frontend's view)
    messages: List[Message]   # full prior history (user/assistant/tool/...)
    tools: List[Tool]         # frontend-provided tools (name, description, JSONSchema params)
    context: List[Context]    # extra prompt context
    forwarded_props: Any
    resume: Optional[List[ResumeEntry]]  # responses to interrupts from prior RUN_FINISHED
```

`Message` is a discriminated union: `DeveloperMessage | SystemMessage | AssistantMessage | UserMessage | ToolMessage | ActivityMessage | ReasoningMessage`. `UserMessage.content` is `Union[str, List[InputContent]]` to support multimodal.

---

## 3. Strands → AG-UI binding

The official integration lives at `integrations/aws-strands/python` in the AG-UI repo. It is published as the PyPI package **`ag_ui_strands`** (`0.1.8`, depends on `ag-ui-protocol>=0.1.18` and `strands-agents>=1.15.0`).

The public surface is exactly six names (from `ag_ui_strands/__init__.py`):

```python
from ag_ui_strands import (
    StrandsAgent,                  # wraps strands.Agent
    create_strands_app,            # factory: returns FastAPI app
    add_strands_fastapi_endpoint,  # mount on existing FastAPI
    StrandsAgentConfig,            # per-agent config (state builder, tool behaviors)
    ToolBehavior,                  # per-tool behavior overrides
    ToolCallContext, ToolResultContext, PredictStateMapping,
)
```

### How events are emitted

There is **no `agui_stream(agent)` adapter**. The model is: `StrandsAgent` is itself the adapter — instantiate it around a `strands.Agent`, call `agent.run(input_data: RunAgentInput)`, and it yields a `Generator[Event, None, None]` of AG-UI events directly. The FastAPI helper just SSE-encodes that generator. From `endpoint.py`:

```python
@app.post(path)
async def strands_endpoint(input_data: RunAgentInput, request: Request):
    encoder = EventEncoder(accept=request.headers.get("accept"))
    async def event_generator():
        async for event in agent.run(input_data):
            yield encoder.encode(event)
    return StreamingResponse(event_generator(), media_type=encoder.get_content_type())
```

`agent.run` wraps `strands.Agent.stream_async()` and translates each native Strands event into the right AG-UI shape (`ARCHITECTURE.md` is the authoritative description):

| Strands signal | AG-UI emission |
|---|---|
| `{"data": ...}` | open `TEXT_MESSAGE_START` if not open, then `TEXT_MESSAGE_CONTENT` chunks |
| `{"reasoningText": ..., "reasoning": true}` | `REASONING_START` → `REASONING_MESSAGE_START` → ... → `REASONING_END` |
| `{"reasoningRedactedContent": ...}` | `REASONING_ENCRYPTED_VALUE` (base64) |
| `current_tool_use` | `TOOL_CALL_START` → `TOOL_CALL_ARGS`* → `TOOL_CALL_END` (+ optional `PredictState` `CUSTOM`) |
| `message.content[].toolResult` | `TOOL_CALL_RESULT`, then `MESSAGES_SNAPSHOT` |
| `multiagent_node_start` / `_stop` | `STEP_STARTED` / `STEP_FINISHED` with `stepName="{node_type}:{node_id}"` |
| `multiagent_handoff` | `CustomEvent(name="MultiAgentHandoff", value={from_nodes, to_nodes, message})` |
| `complete` or halt | close any open text/reasoning, emit `RUN_FINISHED` |
| Any exception | `RUN_ERROR` with `code="STRANDS_ERROR"` |

The adapter also caches `strands.Agent` instances **per `thread_id`** and rebuilds the SDK's internal `messages` list from `RunAgentInput.messages` before each `stream_async` (the "history reconciliation" behavior described in `ARCHITECTURE.md`). This means **the runner can be stateless across turns** — the canonical history lives on the client (or in our backend), and we reseed it on every `POST`. Important: our backend wants to **own** that history (not the client), so on `POST` we'll inject the persisted message log into `RunAgentInput.messages` before forwarding to the runner.

### File layout from the official scaffold

`CopilotKit/with-strands-python` (the `npx copilotkit create -f aws-strands-py` template; repo archived 2026-03-12, now lives at `examples/integrations/strands-python` inside `CopilotKit/CopilotKit`) layout:

```
.
├── agent/                         # Python FastAPI runner
│   ├── main.py                    # builds Strands Agent + StrandsAgent + create_strands_app
│   ├── pyproject.toml
│   └── uv.lock
├── src/
│   ├── app/
│   │   ├── api/copilotkit/route.ts   # Next.js API route → CopilotRuntime → HttpAgent
│   │   ├── layout.tsx                # <CopilotKit runtimeUrl="/api/copilotkit" agent="strands_agent">
│   │   ├── page.tsx                  # <CopilotSidebar>, useCoAgent, useFrontendTool
│   │   └── globals.css
│   └── components/                   # WeatherCard, default-tool-ui, etc.
├── public/
├── scripts/
│   ├── run-agent.sh / .bat           # uvicorn main:app --reload
│   └── setup-agent.sh / .bat         # uv sync inside agent/
├── package.json                   # next, react, @copilotkit/react-{core,ui}, @copilotkit/runtime, @ag-ui/client
├── next.config.ts, tsconfig.json, eslint.config.mjs, postcss.config.mjs
├── .gitignore
└── README.md (archive notice; redirects to CopilotKit monorepo)
```

`package.json` script convention: `npm run dev` spawns `dev:ui` (next dev) and `dev:agent` (`scripts/run-agent.sh` → uvicorn) concurrently via `concurrently --kill-others`. The Python entrypoint (`agent/main.py`) ends with:

```python
app = create_strands_app(agui_agent, "/")  # mounts POST "/" + GET "/ping"
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("AGENT_PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
```

For our project the runner won't expose a FastAPI HTTP surface to the outside world — it'll talk to **the backend** only — but the same `StrandsAgent` adapter applies, and we may keep `create_strands_app` for the subprocess-RPC variant.

---

## 4. CopilotKit ↔ AG-UI (frontend side)

CopilotKit is **not** a direct AG-UI consumer. It introduces a thin runtime layer in Next.js:

```
Browser <— React state —> @copilotkit/react-core hooks
              │
              │ HTTP POST (RPC-ish)
              ▼
      /api/copilotkit  (Next.js route)
              │
              │  uses @copilotkit/runtime
              │  which uses @ag-ui/client.HttpAgent
              │
              ▼ HTTP POST  RunAgentInput  /  reads SSE  AG-UI events
       FastAPI agent endpoint (kloc-agent backend)
```

So `@copilotkit/runtime` is **what speaks AG-UI**; `@copilotkit/react-*` doesn't see AG-UI events directly. The Next.js route file from the scaffold (verbatim):

```typescript
import {
  CopilotRuntime,
  ExperimentalEmptyAdapter,
  copilotRuntimeNextJSAppRouterEndpoint,
} from "@copilotkit/runtime";
import { HttpAgent } from "@ag-ui/client";
import { NextRequest } from "next/server";

const serviceAdapter = new ExperimentalEmptyAdapter();
const runtime = new CopilotRuntime({
  agents: {
    strands_agent: new HttpAgent({ url: process.env.STRANDS_AGENT_URL || "http://localhost:8000" }),
  },
});

export const POST = async (req: NextRequest) => {
  const { handleRequest } = copilotRuntimeNextJSAppRouterEndpoint({
    runtime, serviceAdapter, endpoint: "/api/copilotkit",
  });
  return handleRequest(req);
};
```

`HttpAgent({ url })` POSTs `RunAgentInput`, then iterates SSE events.

### What the React components consume

`@copilotkit/react-ui` provides ready chat shells:
- `<CopilotChat />` — full chat pane
- `<CopilotSidebar />` — pinned-right collapsible chat (the scaffold default)
- `<CopilotPopup />` — floating chat button
- `<CopilotTextarea />` — autocompleting input

These render `TEXT_MESSAGE_*` deltas, `TOOL_CALL_*` cards, and `ACTIVITY_*` progress items automatically. They subscribe to the runtime state, not to AG-UI events directly.

`@copilotkit/react-core` hooks expose the agent state per-agent:

```tsx
// shared state — bound to STATE_SNAPSHOT / STATE_DELTA on the wire
const { state, setState } = useCoAgent<AgentState>({
  name: "strands_agent",
  initialState: { proverbs: [...] },
});

// frontend-only tool: emits a Tool in RunAgentInput.tools, handles
// TOOL_CALL_END on this name client-side
useFrontendTool({
  name: "set_theme_color",
  parameters: [{ name: "theme_color", required: true }],
  handler: ({ theme_color }) => setThemeColor(theme_color),
});

// custom render for a server-side tool — receives streamed args
useRenderToolCall({
  name: "get_weather",
  parameters: [{ name: "location", required: true }],
  render: (props) => <WeatherCard location={props.args.location} />,
});

// default render for unmapped tool calls
useDefaultTool({ render: (props) => <DefaultToolComponent {...props} /> });
```

The provider at the root binds the runtime URL and the active agent name:

```tsx
<CopilotKit runtimeUrl="/api/copilotkit" agent="strands_agent">
  {children}
</CopilotKit>
```

DX summary: developers write React components, declare frontend-only tools and tool renderers as hooks, and the AG-UI events flow into those hooks via the CopilotKit runtime. Generative UI = `useRenderToolCall` with a custom React component receiving streamed tool args.

---

## 5. What `npx copilotkit create -f aws-strands-py` actually ships

The generator clones the `CopilotKit/with-strands-python` template (now consolidated under `CopilotKit/CopilotKit @ examples/integrations/strands-python`). Confirmed by:

- Direct inspection of the (archived) source repo on `main`.
- The CopilotKit/CopilotKit monorepo notice in the archived README.

What you get out of the box:

| Layer | Files | What it does |
|---|---|---|
| Python runner | `agent/main.py`, `agent/pyproject.toml` | A `strands.Agent` with three demo tools (`get_weather`, `set_theme_color`, `update_proverbs`), wrapped in `StrandsAgent`, mounted via `create_strands_app(agui_agent, "/")`. |
| Python deps | `agent/pyproject.toml` | `ag-ui-protocol>=0.1.5`, `fastapi>=0.115.12`, `uvicorn>=0.34.3`, `strands-agents[OpenAI]>=1.15.0`, `strands-agents-tools>=0.2.14`, `ag_ui_strands~=0.1.0` |
| Next.js API bridge | `src/app/api/copilotkit/route.ts` | `CopilotRuntime` registers `strands_agent: new HttpAgent({ url: STRANDS_AGENT_URL })`. |
| Next.js shell | `src/app/layout.tsx` | Wraps children in `<CopilotKit runtimeUrl="/api/copilotkit" agent="strands_agent">`. |
| Next.js page | `src/app/page.tsx` | `<CopilotSidebar>`, `useCoAgent`, `useFrontendTool("set_theme_color")`, `useRenderToolCall("get_weather")`. |
| Frontend deps | `package.json` | `@ag-ui/client@^0.0.42`, `@copilotkit/react-core@1.52.1`, `@copilotkit/react-ui@1.52.1`, `@copilotkit/runtime@1.52.1`, `next@16.0.8`, `react@^19.2.1`, `zod`. |
| Dev scripts | `scripts/run-agent.sh`, `scripts/setup-agent.sh` (and `.bat` siblings) | `uv sync` then `uvicorn main:app --reload`. |
| Concurrency | `package.json` `dev` script | `concurrently "npm run dev:ui" "npm run dev:agent" --kill-others` |
| Env | `agent/.env` | `OPENAI_API_KEY=...` (only required var by default). `STRANDS_AGENT_URL` optional, defaults to `http://localhost:8000`. `AGENT_PATH`, `AGENT_PORT` optional. |

### What we keep vs replace

- **Keep**: the entire `src/` Next.js layout, the API route shape, `<CopilotKit>` provider, `useCoAgent` / `useFrontendTool` / `useRenderToolCall` hooks, the `concurrently`-based dev script.
- **Replace**: `agent/main.py` becomes the per-session runner entrypoint (not a single global). The Next.js API route is rewired so `STRANDS_AGENT_URL` points at our **kloc-agent backend** session-stream endpoint, not directly at a Strands FastAPI app. The backend then proxies to the per-session runner.
- **Augment**: add a `src/lib/api.ts` for session lifecycle REST calls. Add an `src/app/(history)/[sessionId]/page.tsx` for resume-on-reload. Add auth shim later.

---

## 6. Session lifecycle REST API

```
# --- Session lifecycle (JSON, no streaming) ----------------------------------

POST   /v1/sessions
  body: { user_id?, system_prompt?, metadata? }
  201:  { session_id: "ses_01HM...", thread_id: "ses_01HM...", created_at: <iso8601>, status: "open" }

GET    /v1/sessions/{session_id}
  200:  { session_id, thread_id, status: "open"|"closed"|"archived",
          created_at, last_activity_at, message_count, runner_state: "fresh"|"warm"|"evicted" }

GET    /v1/sessions/{session_id}/messages?after={cursor}&limit=100&include={events|messages}
  200:  { messages: [Message, ...], next_cursor: "evt_01HN...", has_more: bool }
  # Cursor is an opaque event id (monotonic). `include=events` returns the raw
  # AG-UI event log; default returns post-finalized AG-UI Message objects.

POST   /v1/sessions/{session_id}/messages
  body: UserMessage  # {id, role:"user", content: string | InputContent[]}
  202:  { run_id: "run_01HM...", stream_url: "/v1/sessions/{session_id}/stream?run_id=..." }
  # Persists the message; returns a stream URL to subscribe to the agent's events.

POST   /v1/sessions/{session_id}/close
  204:  # idempotent

POST   /v1/sessions/{session_id}/runs/{run_id}/cancel
  204:  # signal runner to halt; backend will see RUN_ERROR(code="CANCELLED")
  # We could also expose this as DELETE /runs/{run_id}.

# --- Streaming endpoint ------------------------------------------------------

GET    /v1/sessions/{session_id}/stream?run_id={run_id}&last_event_id={evt_id}
  200:  Content-Type: text/event-stream
  # SSE stream of AG-UI events. last_event_id (or HTTP Last-Event-ID header)
  # makes resume idempotent — backend skips events ≤ last_event_id.

POST   /v1/sessions/{session_id}/stream
  body: RunAgentInput   # AG-UI canonical payload
  200:  Content-Type: text/event-stream
  # AG-UI-compatible: the CopilotKit HttpAgent POSTs this exact shape.
  # If our REST flow already wrote the user message, we still accept this
  # POST to harmonize with HttpAgent; backend de-dupes via run_id.
```

Naming/auth/lifecycle decisions:

- `/v1/` prefix from day one — we'll add v2 when AG-UI ships breaking changes.
- All endpoints require `Authorization: Bearer <session_token>`; session tokens minted at `POST /v1/sessions` (PoC: single hardcoded analyst token).
- Streaming endpoint is `GET` (one stream per session+run) **and** `POST` (CopilotKit-compatible) — both yield the same SSE wire format.
- Cursors are opaque event IDs, monotonically increasing within a session.
- `POST /sessions/{id}/messages` is **synchronous to persist** (202 after DB commit) and **async to stream**. The frontend connects to the returned `stream_url` via `EventSource` to consume.

For the CopilotKit-canonical path the frontend's Next.js route just rewrites `/api/copilotkit` POSTs onto `POST /v1/sessions/{id}/stream` after our `HttpAgent` is configured with that URL. We **don't** need CopilotKit to know about session creation — that happens out-of-band.

---

## 7. Resume / replay

### What "resume" must guarantee

1. The analyst sees the entire prior transcript, in order, with tool calls / state intact, on tab open.
2. Any *in-flight* run that was streaming when the tab closed continues streaming — the new tab catches up to current.
3. If the runner was evicted, the backend spawns a new one transparently. The new runner **must not** re-execute side-effecting tools that already ran; only continue from the next pending step.

### Wire-level mechanics

**On tab open (cold), client does:**

```
GET /v1/sessions/{id}                         → session metadata
GET /v1/sessions/{id}/messages?limit=100      → first page of messages (paginate as needed)
GET /v1/sessions/{id}/stream?last_event_id=…  → SSE; backend pushes nothing for a closed run
```

**On tab open with an in-flight run, client does the same. Backend:**

```
[client opens SSE]
  ── backend opens generator
  ── replays from last_event_id+1 from store
        all replayed events get a marker:  meta: replay  (see below)
  ── once caught up, forwards live runner events as-is
[client disconnects]
  ── runner keeps producing; backend keeps persisting; events not forwarded
[client reconnects]
  ── same loop
```

**Replay vs live — how the frontend knows:**

Option A (what we adopt): we **don't** use a separate event type. Instead, every event the backend persists carries a `timestamp` field (already in `BaseEvent`). On resume, the backend re-emits the persisted events with their original timestamps; live events get current timestamps. The CopilotKit runtime treats a `MESSAGES_SNAPSHOT` as canonical, so any "did we already render this?" reconciliation happens via message id de-dup on the React side.

Option B (fallback if Option A has UX issues): use a `CustomEvent(name="ReplayBoundary")` between the replay segment and the live segment, so the client can show a "you missed N events" banner. We'll start with A.

**If runner was evicted and we need to rehydrate:**

```
POST /v1/sessions/{id}/messages   user types
  backend persists user msg
  spawns new runner for session_id
  runner.run(RunAgentInput) where:
      thread_id = session_id
      messages   = full history from DB (including stale tool results)
      state      = last persisted STATE_SNAPSHOT (replaying patches)
      tools      = frontend-declared tools from the in-bound payload
  → StrandsAgent caches a fresh strands.Agent for this thread,
    rebuilds Strands' internal messages from RunAgentInput.messages (the
    "history reconciliation" path), then runs.
  → emits RUN_STARTED, MESSAGES_SNAPSHOT (matches DB), then proceeds.
```

The first three events from a rehydrated runner will look identical to a normal run start to the client:

```json
{"type":"RUN_STARTED","threadId":"ses_01HM...","runId":"run_01HN..."}
{"type":"STATE_SNAPSHOT","snapshot":{...last_persisted_state}}
{"type":"MESSAGES_SNAPSHOT","messages":[/* full history */]}
```

The Strands adapter's behavior of replaying `MESSAGES_SNAPSHOT` at lifecycle boundaries (after `STATE_SNAPSHOT`, after each `TOOL_CALL_END`/`TOOL_CALL_RESULT`/`TEXT_MESSAGE_END`) is exactly what we need — the client's view is reconstructed from snapshots, not from incremental events. Cited from `integrations/aws-strands/ARCHITECTURE.md`:

> Each snapshot carries the complete thread state as known so far.

### What we explicitly do NOT do

- We don't snapshot the Strands `Agent`'s internal Python state to disk. The runner is **ephemeral**; durability is the backend's job. This is also what the Strands adapter assumes when `session_manager` is absent (it rebuilds from `RunAgentInput.messages`).
- We don't try to resume mid-tool-call. If a runner dies while a tool is running, the call is lost and the next runner will see no `TOOL_CALL_RESULT` for that `toolCallId` — we surface this as a `RUN_ERROR` to the client and the analyst retries.

---

## 8. Hook webhook receiver

Runners POST lifecycle events to the backend so we can audit, enforce policy, and feed observability without involving the streaming path. Strands native hooks (`BeforeToolCallEvent`, etc.) wrap an HTTP POST per event.

### Shape

```
POST  /v1/webhooks/runners/{runner_id}/events
Headers:
  Authorization: HMAC <signature>     # see auth below
  Content-Type: application/json
  X-Kloc-Hook-Event: BeforeToolCall   # also redundant in body for indexing
  X-Kloc-Hook-Ts: 1747200001234       # unix ms; for replay detection
Body:
  {
    "event": "BeforeToolCall",
    "runner_id": "runner_01HM...",
    "session_id": "ses_01HM...",
    "run_id": "run_01HM...",
    "timestamp": 1747200001234,
    "payload": { /* event-specific; see below */ }
  }
202 Accepted
  { "decision": "allow" | "deny", "reason"?: "<string>" }
```

The response carries a synchronous policy decision so the runner can block the tool call inline. For decisions we *don't* need to block on (after-the-fact audit), the runner POSTs `fire-and-forget` style; we still validate auth and persist.

### Auth: HMAC over body + timestamp

Shared secret per-runner provisioned at spawn time. The runner signs:

```
signature = base64(HMAC_SHA256(secret, f"{timestamp}.{raw_body}"))
header    = Authorization: HMAC <signature>
```

Backend rejects requests where `|now - timestamp| > 60s` (replay window) or where the HMAC doesn't match. Per-runner secret avoids needing per-call tokens and makes revocation trivial (kill the runner → secret is gone).

### Payload variants

```json
// BeforeToolCall
{ "event":"BeforeToolCall", "payload":{
    "tool_call_id":"call_42",
    "tool_name":"kloc.search",
    "args":{"query":"top callers of Order::dispatch"}
}}

// AfterToolCall
{ "event":"AfterToolCall", "payload":{
    "tool_call_id":"call_42",
    "tool_name":"kloc.search",
    "duration_ms":312,
    "result_preview":"<truncated 1024-char JSON>",
    "error":null
}}

// BeforeInvocation / AfterInvocation
{ "event":"BeforeInvocation", "payload":{
    "user_message_id":"msg_u1",
    "context":{...}
}}

// RunnerHeartbeat
{ "event":"RunnerHeartbeat", "payload":{
    "uptime_s": 480, "active_run_ids": ["run_01HM..."],
    "mem_bytes": 178257920
}}
```

### Failure mode / back-pressure

If the backend is slow:

- Webhooks have a **2-second timeout**. Past that, the runner caches the event to local disk and retries asynchronously (exponential backoff). Critical hooks (`BeforeToolCall`) escalate to deny-by-default if no response in 2 s.
- Runners hold a bounded async queue (default 256) for fire-and-forget hooks. When it fills, the runner enters degraded mode: drop heartbeats first, then `AfterToolCall` previews (but never `BeforeToolCall`), and emit a single `CustomEvent(name="HookBackpressure", value={...})` to the stream so the audit chain has a record of the drop.

### Why not embed hooks in the SSE stream?

- Separation of concerns: the stream is for the **user**, hooks are for the **operator**. Mixing them means the frontend has to filter, and a Hook-failure-aware client is annoying to write.
- Separation of trust: webhook auth is HMAC-with-runner-secret; streaming auth is the analyst's session token. Different lifecycles, different rotation policies.
- Independent persistence schemas: hook events go to an audit table with retention; stream events live in the session log with shorter retention.

---

## 9. Round-trip diagram — one user message

```
┌─────────┐                                                                            
│Analyst  │ types "find callers of Order::dispatch", hits Enter                        
└────┬────┘                                                                            
     │                                                                                 
     │ React onSubmit handler                                                          
     ▼                                                                                 
┌──────────────────────────┐                                                           
│ Next.js page (browser)   │                                                           
│ <CopilotChat>            │                                                           
│ @copilotkit/react-core   │                                                           
└────┬─────────────────────┘                                                           
     │                                                                                 
     │ (1) HTTP POST /api/copilotkit                                                   
     │     body: AG-UI HttpAgent payload                                               
     ▼                                                                                 
┌──────────────────────────┐                                                           
│ Next.js route.ts         │                                                           
│ CopilotRuntime           │                                                           
│ + HttpAgent({ url:       │                                                           
│   KLOC_BACKEND_URL })    │                                                           
└────┬─────────────────────┘                                                           
     │                                                                                 
     │ (2) HTTP POST /v1/sessions/{id}/stream                                          
     │     body: RunAgentInput {thread_id, run_id,                                     
     │             messages:[...prior, NEW UserMessage],                               
     │             state, tools:[frontend-only], context}                              
     │     Accept: text/event-stream                                                   
     ▼                                                                                 
┌──────────────────────────┐                                                           
│ FastAPI                  │     ┌────────────────┐                                    
│ kloc-agent backend       │────▶│  PostgreSQL    │ (3) INSERT user message            
│                          │     │  sessions,     │     INSERT run row                 
│ ┌──────────────────────┐ │     │  messages,     │     (commit BEFORE forwarding)     
│ │ /v1/sessions/{id}/   │ │     │  events,       │                                    
│ │   stream handler     │ │     │  audit         │                                    
│ │ - StreamingResponse  │ │     └────────────────┘                                    
│ │ - async generator    │ │                                                           
│ │ - persist-then-yield │ │                                                           
│ └──────────┬───────────┘ │                                                           
└────────────┼─────────────┘                                                           
             │                                                                         
             │ (4) Spawn or grab runner for session_id                                 
             │     IPC = HTTP loopback to runner FastAPI on a Unix domain socket       
             │     (PoC) — or subprocess stdin+stdout JSON-lines for max isolation.    
             │     Production: gRPC to AgentCore instance.                             
             ▼                                                                         
┌──────────────────────────┐                                                           
│ Runner process (uvicorn) │                                                           
│ ag_ui_strands.StrandsAgent│                                                          
│ wraps strands.Agent       │                                                          
│                          │                                                           
│ ┌──────────────────────┐ │     (5) Strands stream_async()                            
│ │ strands.Agent        │─┼───▶ Anthropic API (or other model)                        
│ │ + MCP tool client    │ │     Tool calls → MCP intelligence service                 
│ │ + skills plugin      │ │     Native events come back.                              
│ └──────────────────────┘ │                                                           
│                          │                                                           
│ (6) Each native Strands  │                                                           
│ event → AG-UI event.     │                                                           
│ yields:                  │                                                           
│   RUN_STARTED            │                                                           
│   STATE_SNAPSHOT         │                                                           
│   MESSAGES_SNAPSHOT      │                                                           
│   TEXT_MESSAGE_START     │                                                           
│   TEXT_MESSAGE_CONTENT*  │                                                           
│   TOOL_CALL_START        │                                                           
│   TOOL_CALL_ARGS*        │                                                           
│   TOOL_CALL_END          │                                                           
│   (tool runs via MCP)    │                                                           
│   TOOL_CALL_RESULT       │                                                           
│   MESSAGES_SNAPSHOT      │                                                           
│   TEXT_MESSAGE_*         │                                                           
│   RUN_FINISHED           │                                                           
└────────────┬─────────────┘                                                           
             │                                                                         
             │ (7) Each event → backend over HTTP/UDS chunked-JSON (one AG-UI          
             │     event per line) — runner is the SSE *producer*, backend is          
             │     the SSE *re-encoder* for the client.                                
             │                                                                         
             │ (parallel) Runner POSTs hook lifecycle events:                          
             │     POST /v1/webhooks/runners/{runner_id}/events                        
             │     {BeforeToolCall, AfterToolCall, ...}                                
             │     → backend writes audit row, returns {decision:"allow"}              
             ▼                                                                         
┌──────────────────────────┐                                                           
│ Backend re-enters the    │                                                           
│ generator loop:          │                                                           
│  for each event:         │                                                           
│    persist to DB         │                                                           
│    encoder.encode(event) │                                                           
│    yield bytes           │                                                           
└────────────┬─────────────┘                                                           
             │                                                                         
             │ (8) HTTP response body, chunked: SSE frames                             
             │     data: {"type":"TEXT_MESSAGE_CONTENT", ...}\n\n                      
             │     data: {"type":"TOOL_CALL_START", ...}\n\n                           
             │     ...                                                                 
             ▼                                                                         
┌──────────────────────────┐                                                           
│ Next.js route handler    │                                                           
│ pipes response through   │                                                           
│ to the browser fetch.    │                                                           
└────────────┬─────────────┘                                                           
             │                                                                         
             │ (9) Browser EventSource (inside @copilotkit/runtime client)             
             │     parses each SSE frame, dispatches into CopilotKit runtime           
             │     store, which fires React re-renders.                                
             ▼                                                                         
┌──────────────────────────┐                                                           
│ <CopilotChat>            │                                                           
│ shows streaming text +   │                                                           
│ tool-call card +         │                                                           
│ generative UI rendered   │                                                           
│ by useRenderToolCall.    │                                                           
└──────────────────────────┘                                                           
             │                                                                         
             │ (10) On RUN_FINISHED, frontend marks the run idle.                      
             │     Backend marks the run row "finished", optionally                    
             │     reaps the runner (or holds it warm for N seconds).                  
             ▼                                                                         
        ──── done ────                                                                 
```

### IPC explicitness

| Hop | Protocol | Why |
|---|---|---|
| (1) Browser → Next.js | HTTPS POST | Standard CopilotKit pattern; nothing custom. |
| (2) Next.js → backend | HTTPS POST + SSE | AG-UI canonical; `HttpAgent` URL points at backend. |
| (3) Backend → DB | SQL (asyncpg) | Persist before forwarding. |
| (4) Backend → runner | HTTP over UDS (PoC) / subprocess stdin/stdout (alt) / gRPC (AgentCore) | Pluggable behind a `Runner` interface. PoC default is loopback HTTP for symmetry with AG-UI. |
| (5) Runner → model API | HTTPS (Anthropic / Bedrock) | Standard. |
| (5b) Runner → MCP | stdio (PoC) or HTTP+SSE (server-mode) | MCP-native. |
| (6) Runner internal | Python generator | `StrandsAgent.run` is `async def` with `yield`. |
| (7) Runner → backend (events) | HTTP chunked JSON-lines | Backend treats this as an event stream from the runner. |
| (7b) Runner → backend (hooks) | HTTPS POST + HMAC | Separate path; sync for `Before*`, async for `After*`. |
| (8) Backend → Next.js | SSE | `EventEncoder` produces this verbatim. |
| (9) Next.js → browser | SSE pass-through | Edge runtime tunnels chunks. |

### Persistence ordering rules (re-emphasized for the implementer)

1. **User message**: persist `Message` row → commit → forward to runner. Never the other way around.
2. **Assistant deltas**: persist each `TEXT_MESSAGE_CONTENT.delta` as an event row (append-only). At `TEXT_MESSAGE_END`, persist the concatenated `AssistantMessage` to the `messages` table.
3. **Tool calls**: at `TOOL_CALL_END`, persist `AssistantMessage(tool_calls=[...])`. At `TOOL_CALL_RESULT`, persist `ToolMessage`.
4. **State**: at every `STATE_SNAPSHOT`, persist a snapshot row. Apply `STATE_DELTA` to the latest snapshot in-memory (or persist deltas if we want full undo history).
5. **Hooks**: persist before responding `{decision:"allow"}` — the runner is *gating* on us, so we can't lose the audit row.

---

## 10. Open questions / unknowns

These are things I could not nail down precisely from the docs:

- **AG-UI protocol version string**: There is no semver on `docs.ag-ui.com`. The closest reference is the Python package version (`ag-ui-protocol==0.1.18`, May 2026) and the `@ag-ui/core` npm package. The spec relies on the discriminated-union event types for compatibility, not a version field. If we need to "advertise our protocol version" in a webhook payload, we'll send the Python package version.
- **`MESSAGES_SNAPSHOT` cost at scale**: the Strands adapter sends a full snapshot at four lifecycle boundaries. For a long-running session with many tool calls, that's O(N) bytes per snapshot × O(N) snapshots = O(N²) bandwidth. If this becomes a problem we can flip `StrandsAgentConfig.emit_messages_snapshot=False` and reconstruct on the client. Not a PoC concern.
- **CopilotKit `HttpAgent` headers**: I couldn't find authoritative docs on whether it forwards custom headers from the React side to the FastAPI endpoint. For auth we should plan on either an HttpAgent-level `headers` config or doing auth at the Next.js route layer and using a service-account-like model between Next.js and backend.
- **`dojo.ag-ui.com` event mix**: I didn't get to inspect the live playground — would be a good sanity check that we cover the events the reference frontends actually receive in the wild.
- **Subprocess vs HTTP for runner IPC**: both work. The Strands ARCHITECTURE doc only describes the HTTP path. For the PoC I'd recommend keeping `ag_ui_strands.create_strands_app` and running the runner as a child uvicorn on a Unix domain socket — closest to upstream, easiest to debug. Subprocess+stdin is more isolated but requires us to handcraft a JSON-lines protocol.

---

## Sources

- AG-UI protocol overview: https://docs.ag-ui.com/
- AG-UI architecture: https://docs.ag-ui.com/concepts/architecture.md
- AG-UI events page: https://docs.ag-ui.com/concepts/events
- AG-UI Python events module: https://github.com/ag-ui-protocol/ag-ui/blob/main/sdks/python/ag_ui/core/events.py
- AG-UI Python types module: https://github.com/ag-ui-protocol/ag-ui/blob/main/sdks/python/ag_ui/core/types.py
- AG-UI Python encoder: https://github.com/ag-ui-protocol/ag-ui/blob/main/sdks/python/ag_ui/encoder/encoder.py
- AG-UI Python pyproject (version 0.1.18): https://github.com/ag-ui-protocol/ag-ui/blob/main/sdks/python/pyproject.toml
- AG-UI documentation index: https://docs.ag-ui.com/llms.txt
- AG-UI messages concept: https://docs.ag-ui.com/concepts/messages.md
- AG-UI state concept: https://docs.ag-ui.com/concepts/state.md
- AG-UI capabilities concept: https://docs.ag-ui.com/concepts/capabilities.md
- AG-UI quickstart server: https://docs.ag-ui.com/quickstart/server.md
- AWS Strands × AG-UI ARCHITECTURE.md: https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/aws-strands/ARCHITECTURE.md
- `ag_ui_strands` package init: https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/aws-strands/python/src/ag_ui_strands/__init__.py
- `ag_ui_strands` endpoint module: https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/aws-strands/python/src/ag_ui_strands/endpoint.py
- `ag_ui_strands` utils (create_strands_app): https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/aws-strands/python/src/ag_ui_strands/utils.py
- `ag_ui_strands` agentic-chat example: https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/aws-strands/python/examples/server/api/agentic_chat.py
- `ag_ui_strands` pyproject (version 0.1.8): https://github.com/ag-ui-protocol/ag-ui/blob/main/integrations/aws-strands/python/pyproject.toml
- Strands × AG-UI community page: https://strandsagents.com/docs/community/integrations/ag-ui/
- CopilotKit AWS Strands docs: https://docs.copilotkit.ai/aws-strands
- CopilotKit `with-strands-python` template (archived, consolidated into monorepo): https://github.com/CopilotKit/with-strands-python
- CopilotKit `with-strands-python` agent/main.py: https://github.com/CopilotKit/with-strands-python/blob/main/agent/main.py
- CopilotKit `with-strands-python` route.ts: https://github.com/CopilotKit/with-strands-python/blob/main/src/app/api/copilotkit/route.ts
- CopilotKit `with-strands-python` layout.tsx: https://github.com/CopilotKit/with-strands-python/blob/main/src/app/layout.tsx
- CopilotKit `with-strands-python` page.tsx: https://github.com/CopilotKit/with-strands-python/blob/main/src/app/page.tsx
- CopilotKit `with-strands-python` package.json: https://github.com/CopilotKit/with-strands-python/blob/main/package.json
- "30-minute" walkthrough: https://dev.to/copilotkit/easily-build-a-frontend-for-your-aws-strands-agents-using-ag-ui-in-30-minutes-42ji
- FastAPI streaming responses: https://fastapi.tiangolo.com/advanced/custom-response/
- FastAPI WebSockets: https://fastapi.tiangolo.com/advanced/websockets/
- AG-UI dojo (not inspected this pass): https://dojo.ag-ui.com
