import type {
  AGUIEvent,
  ArtifactAttachedCustom,
  ArtifactView,
  MessageView,
  PersistedMessage,
  ReducerAction,
  SessionViewState,
  ToolCallDeniedCustom,
  ToolCallView,
} from "./types";

export const INITIAL_STATE: SessionViewState = {
  detail: null,
  messages: [],
  hasMoreHistory: false,
  oldestSeq: null,
  connection: "idle",
  activeRunId: null,
  lastEventSeq: null,
  errorMessage: null,
};

export function persistedToMessageView(m: PersistedMessage): MessageView {
  return {
    id: m.id,
    role: m.role,
    content: m.content ?? "",
    finalized: m.finalized_at !== null,
    toolCalls: (m.tool_calls ?? []).map((t) => ({
      id: t.tool_call_id,
      name: t.tool_name,
      args: t.args ?? "",
      state: t.state,
      result: t.result ?? undefined,
      meta: buildToolMeta(
        t.state,
        t.result ?? undefined,
        t.denied_reason ?? undefined,
        t.denied_hint ?? undefined,
      ),
    })),
    artifacts: [],
    created_at: m.created_at,
    seq: m.seq,
  };
}

function makeAssistantMessage(id: string): MessageView {
  return {
    id,
    role: "assistant",
    content: "",
    finalized: false,
    toolCalls: [],
    artifacts: [],
  };
}

function findLastAssistantIndex(messages: MessageView[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "assistant") return i;
  }
  return -1;
}

function findOpenAssistantIndex(messages: MessageView[]): number {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.role === "assistant" && !m.finalized) return i;
  }
  return -1;
}

function replaceMessage(
  messages: MessageView[],
  index: number,
  next: MessageView,
): MessageView[] {
  const out = messages.slice();
  out[index] = next;
  return out;
}

function ensureAssistantMessage(
  messages: MessageView[],
  messageId: string,
): { messages: MessageView[]; index: number } {
  const existing = messages.findIndex(
    (m) => m.role === "assistant" && m.id === messageId,
  );
  if (existing !== -1) {
    return { messages, index: existing };
  }
  const created = makeAssistantMessage(messageId);
  return { messages: messages.concat(created), index: messages.length };
}

function buildToolMeta(
  state: ToolCallView["state"],
  result?: string,
  deniedReason?: string,
  deniedHint?: string,
): string {
  if (state === "denied") {
    // History-reload path surfaces reason/hint pulled from the persisted
    // row; the live-streaming path passes neither so the "DENIED" label
    // continues to fire unchanged.
    const parts: string[] = ["DENIED"];
    if (deniedReason) parts.push(deniedReason);
    if (deniedHint) parts.push(deniedHint);
    return parts.join(" · ");
  }
  if (state === "running") return "running…";
  if (!result) return "ok";
  const trimmed = result.trim();
  if (!trimmed) return "ok";
  if (trimmed.length < 80) return trimmed;
  return `${trimmed.length} chars`;
}

function applyAGUIEvent(
  state: SessionViewState,
  event: AGUIEvent,
  seq: number,
): SessionViewState {
  const wireSeq = typeof event.seq === "number" ? event.seq : seq;
  const baseLastSeq =
    state.lastEventSeq === null || wireSeq > state.lastEventSeq
      ? wireSeq
      : state.lastEventSeq;

  switch (event.type) {
    case "RUN_STARTED": {
      return {
        ...state,
        connection: "live",
        activeRunId: event.runId,
        errorMessage: null,
        lastEventSeq: baseLastSeq,
      };
    }
    case "RUN_FINISHED": {
      const messages = state.messages.map((m) =>
        m.role === "assistant" && !m.finalized ? { ...m, finalized: true } : m,
      );
      return {
        ...state,
        messages,
        connection: "idle",
        activeRunId: null,
        lastEventSeq: baseLastSeq,
      };
    }
    case "RUN_ERROR": {
      return {
        ...state,
        connection: "error",
        activeRunId: null,
        errorMessage: event.message || "Assistant failed",
        lastEventSeq: baseLastSeq,
      };
    }
    case "TEXT_MESSAGE_START": {
      const { messages, index } = ensureAssistantMessage(
        state.messages,
        event.messageId,
      );
      return {
        ...state,
        messages: replaceMessage(messages, index, {
          ...messages[index],
          finalized: false,
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "TEXT_MESSAGE_CONTENT": {
      const { messages, index } = ensureAssistantMessage(
        state.messages,
        event.messageId,
      );
      const target = messages[index];
      return {
        ...state,
        messages: replaceMessage(messages, index, {
          ...target,
          content: target.content + event.delta,
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "TEXT_MESSAGE_END": {
      const idx = state.messages.findIndex(
        (m) => m.role === "assistant" && m.id === event.messageId,
      );
      if (idx === -1) return { ...state, lastEventSeq: baseLastSeq };
      return {
        ...state,
        messages: replaceMessage(state.messages, idx, {
          ...state.messages[idx],
          finalized: true,
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "TOOL_CALL_START": {
      const targetId = event.parentMessageId;
      let workingMessages = state.messages;
      let parentIdx: number;
      if (targetId) {
        const r = ensureAssistantMessage(workingMessages, targetId);
        workingMessages = r.messages;
        parentIdx = r.index;
      } else {
        parentIdx = findOpenAssistantIndex(workingMessages);
        if (parentIdx === -1) {
          if (typeof console !== "undefined") {
            console.warn(
              "reducer.tool_call_start.no_parent",
              { toolCallId: event.toolCallId, toolName: event.toolCallName },
            );
          }
          return { ...state, lastEventSeq: baseLastSeq };
        }
      }
      const parent = workingMessages[parentIdx];
      const existing = parent.toolCalls.find((t) => t.id === event.toolCallId);
      if (existing) {
        return { ...state, messages: workingMessages, lastEventSeq: baseLastSeq };
      }
      const newCall: ToolCallView = {
        id: event.toolCallId,
        name: event.toolCallName,
        args: "",
        state: "running",
        meta: buildToolMeta("running"),
      };
      return {
        ...state,
        messages: replaceMessage(workingMessages, parentIdx, {
          ...parent,
          toolCalls: parent.toolCalls.concat(newCall),
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "TOOL_CALL_ARGS": {
      const { messages, parentIdx, callIdx } = locateToolCall(
        state.messages,
        event.toolCallId,
      );
      if (parentIdx === -1 || callIdx === -1) {
        return { ...state, lastEventSeq: baseLastSeq };
      }
      const parent = messages[parentIdx];
      const call = parent.toolCalls[callIdx];
      const nextCalls = parent.toolCalls.slice();
      nextCalls[callIdx] = { ...call, args: call.args + event.delta };
      return {
        ...state,
        messages: replaceMessage(messages, parentIdx, {
          ...parent,
          toolCalls: nextCalls,
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "TOOL_CALL_END": {
      const { messages, parentIdx, callIdx } = locateToolCall(
        state.messages,
        event.toolCallId,
      );
      if (parentIdx === -1 || callIdx === -1) {
        return { ...state, lastEventSeq: baseLastSeq };
      }
      const parent = messages[parentIdx];
      const call = parent.toolCalls[callIdx];
      if (call.state === "denied") {
        return { ...state, lastEventSeq: baseLastSeq };
      }
      const nextCalls = parent.toolCalls.slice();
      nextCalls[callIdx] = {
        ...call,
        state: "done",
        meta: buildToolMeta("done", call.result),
      };
      return {
        ...state,
        messages: replaceMessage(messages, parentIdx, {
          ...parent,
          toolCalls: nextCalls,
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "TOOL_CALL_RESULT": {
      const { messages, parentIdx, callIdx } = locateToolCall(
        state.messages,
        event.toolCallId,
      );
      if (parentIdx === -1 || callIdx === -1) {
        return { ...state, lastEventSeq: baseLastSeq };
      }
      const parent = messages[parentIdx];
      const call = parent.toolCalls[callIdx];
      const nextCalls = parent.toolCalls.slice();
      nextCalls[callIdx] = {
        ...call,
        result: event.content,
        meta:
          call.state === "denied"
            ? buildToolMeta("denied", event.content)
            : buildToolMeta(call.state, event.content),
      };
      return {
        ...state,
        messages: replaceMessage(messages, parentIdx, {
          ...parent,
          toolCalls: nextCalls,
        }),
        lastEventSeq: baseLastSeq,
      };
    }
    case "STATE_SNAPSHOT": {
      return { ...state, lastEventSeq: baseLastSeq };
    }
    case "CUSTOM": {
      return applyCustom(state, event, baseLastSeq);
    }
    default: {
      const _exhaustive: never = event;
      void _exhaustive;
      return { ...state, lastEventSeq: baseLastSeq };
    }
  }
}

function locateToolCall(
  messages: MessageView[],
  toolCallId: string,
): { messages: MessageView[]; parentIdx: number; callIdx: number } {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    const idx = m.toolCalls.findIndex((t) => t.id === toolCallId);
    if (idx !== -1) {
      return { messages, parentIdx: i, callIdx: idx };
    }
  }
  return { messages, parentIdx: -1, callIdx: -1 };
}

function applyCustom(
  state: SessionViewState,
  event: Extract<AGUIEvent, { type: "CUSTOM" }>,
  lastEventSeq: number,
): SessionViewState {
  if (event.name === "ToolCallDenied") {
    const value = (event as ToolCallDeniedCustom).value;
    const located = locateToolCall(state.messages, value.toolCallId);
    if (located.parentIdx !== -1 && located.callIdx !== -1) {
      const parent = located.messages[located.parentIdx];
      const call = parent.toolCalls[located.callIdx];
      const nextCalls = parent.toolCalls.slice();
      nextCalls[located.callIdx] = {
        ...call,
        state: "denied",
        result: value.reason,
        meta: "DENIED",
      };
      return {
        ...state,
        messages: replaceMessage(located.messages, located.parentIdx, {
          ...parent,
          toolCalls: nextCalls,
        }),
        lastEventSeq,
      };
    }
    let workingMessages = state.messages;
    let parentIdx = -1;
    if (value.parentMessageId) {
      const r = ensureAssistantMessage(workingMessages, value.parentMessageId);
      workingMessages = r.messages;
      parentIdx = r.index;
    } else {
      parentIdx = findOpenAssistantIndex(workingMessages);
      if (parentIdx === -1) {
        parentIdx = findLastAssistantIndex(workingMessages);
      }
    }
    if (parentIdx === -1) {
      if (typeof console !== "undefined") {
        console.warn(
          "reducer.tool_call_denied.no_parent",
          { toolCallId: value.toolCallId, toolName: value.toolName },
        );
      }
      return { ...state, lastEventSeq };
    }
    const parent = workingMessages[parentIdx];
    const synthetic: ToolCallView = {
      id: value.toolCallId,
      name: value.toolName,
      args: "",
      state: "denied",
      result: value.reason,
      meta: "DENIED",
    };
    return {
      ...state,
      messages: replaceMessage(workingMessages, parentIdx, {
        ...parent,
        toolCalls: parent.toolCalls.concat(synthetic),
      }),
      lastEventSeq,
    };
  }

  if (event.name === "ArtifactAttached") {
    const value = (event as ArtifactAttachedCustom).value;
    const artifact: ArtifactView = {
      id: value.artifactId,
      filename: value.filename,
      size_bytes: value.sizeBytes,
      content_type: value.mimeType ?? "",
    };
    let workingMessages = state.messages;
    let parentIdx = -1;
    if (value.parentMessageId) {
      const r = ensureAssistantMessage(workingMessages, value.parentMessageId);
      workingMessages = r.messages;
      parentIdx = r.index;
    } else {
      parentIdx = findOpenAssistantIndex(workingMessages);
      if (parentIdx === -1) parentIdx = findLastAssistantIndex(workingMessages);
    }
    if (parentIdx === -1) {
      const synthetic = makeAssistantMessage(`assistant:${artifact.id}`);
      workingMessages = workingMessages.concat(synthetic);
      parentIdx = workingMessages.length - 1;
    }
    const parent = workingMessages[parentIdx];
    if (parent.artifacts.some((a) => a.id === artifact.id)) {
      return { ...state, messages: workingMessages, lastEventSeq };
    }
    return {
      ...state,
      messages: replaceMessage(workingMessages, parentIdx, {
        ...parent,
        artifacts: parent.artifacts.concat(artifact),
      }),
      lastEventSeq,
    };
  }

  return { ...state, lastEventSeq };
}

export function applyAction(
  state: SessionViewState,
  action: ReducerAction,
): SessionViewState {
  switch (action.type) {
    case "HYDRATE_DETAIL":
      return { ...state, detail: action.detail };
    case "HYDRATE_MESSAGES":
      return {
        ...state,
        messages: action.messages,
        hasMoreHistory: action.hasMore,
        oldestSeq: action.oldestSeq,
      };
    case "OPTIMISTIC_USER_MESSAGE":
      return { ...state, messages: state.messages.concat(action.message) };
    case "ROLLBACK_OPTIMISTIC":
      return {
        ...state,
        messages: state.messages.filter((m) => m.id !== action.messageId),
      };
    case "AGUI_EVENT":
      return applyAGUIEvent(state, action.event, action.seq);
    case "SET_CONNECTION":
      return {
        ...state,
        connection: action.state,
        errorMessage:
          action.error === undefined ? state.errorMessage : action.error,
      };
    case "RESET_RUN":
      return {
        ...state,
        connection: "idle",
        activeRunId: null,
        errorMessage: null,
      };
    default: {
      const _exhaustive: never = action;
      void _exhaustive;
      return state;
    }
  }
}
