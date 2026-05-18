export type RunnerState = "idle" | "spawning" | "running" | "closing";

export interface SessionListItem {
  id: string;
  title: string;
  runner_state: RunnerState;
  message_count: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

export interface SessionList {
  sessions: SessionListItem[];
}

export interface SessionDetail {
  id: string;
  analyst_id: string;
  title: string;
  runner_state: RunnerState;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

export type MessageRole = "user" | "assistant" | "system" | "tool";

export interface PersistedMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  content_parts: Record<string, unknown> | null;
  model: string | null;
  seq: number;
  created_at: string;
  finalized_at: string | null;
}

export interface MessagesPage {
  messages: PersistedMessage[];
  next_cursor: number | null;
  has_more: boolean;
}

export interface CreateSessionResponse {
  session_id: string;
  created_at: string;
}

export interface PostMessageResponse {
  run_id: string;
  message_id: string;
  stream_url: string;
}

export type AGUIEventType =
  | "RUN_STARTED"
  | "RUN_FINISHED"
  | "RUN_ERROR"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "TEXT_MESSAGE_END"
  | "TOOL_CALL_START"
  | "TOOL_CALL_ARGS"
  | "TOOL_CALL_END"
  | "TOOL_CALL_RESULT"
  | "STATE_SNAPSHOT"
  | "CUSTOM";

export interface BaseEvent<T extends AGUIEventType> {
  type: T;
  timestamp?: number;
  seq?: number;
}

export interface RunStartedEvent extends BaseEvent<"RUN_STARTED"> {
  threadId: string;
  runId: string;
}

export interface RunFinishedEvent extends BaseEvent<"RUN_FINISHED"> {
  threadId: string;
  runId: string;
}

export interface RunErrorEvent extends BaseEvent<"RUN_ERROR"> {
  threadId: string;
  runId: string;
  code: string;
  message: string;
  cause?: string;
}

export interface TextMessageStartEvent extends BaseEvent<"TEXT_MESSAGE_START"> {
  messageId: string;
  role: "assistant";
}

export interface TextMessageContentEvent
  extends BaseEvent<"TEXT_MESSAGE_CONTENT"> {
  messageId: string;
  delta: string;
}

export interface TextMessageEndEvent extends BaseEvent<"TEXT_MESSAGE_END"> {
  messageId: string;
}

export interface ToolCallStartEvent extends BaseEvent<"TOOL_CALL_START"> {
  toolCallId: string;
  toolCallName: string;
  parentMessageId?: string;
}

export interface ToolCallArgsEvent extends BaseEvent<"TOOL_CALL_ARGS"> {
  toolCallId: string;
  delta: string;
}

export interface ToolCallEndEvent extends BaseEvent<"TOOL_CALL_END"> {
  toolCallId: string;
}

export interface ToolCallResultEvent extends BaseEvent<"TOOL_CALL_RESULT"> {
  toolCallId: string;
  messageId?: string;
  content: string;
}

export interface StateSnapshotEvent extends BaseEvent<"STATE_SNAPSHOT"> {
  snapshot: Record<string, unknown>;
}

export interface ToolCallDeniedCustom extends BaseEvent<"CUSTOM"> {
  name: "ToolCallDenied";
  value: { toolCallId: string; toolName: string; reason: string };
}

export interface ArtifactAttachedCustom extends BaseEvent<"CUSTOM"> {
  name: "ArtifactAttached";
  value: {
    artifactId: string;
    filename: string;
    sizeBytes: number;
    mimeType?: string;
    parentMessageId?: string;
  };
}

export interface HookBackpressureCustom extends BaseEvent<"CUSTOM"> {
  name: "HookBackpressure";
  value: Record<string, unknown>;
}

export interface UnknownCustomEvent extends BaseEvent<"CUSTOM"> {
  name: string;
  value: unknown;
}

export type CustomAGUIEvent =
  | ToolCallDeniedCustom
  | ArtifactAttachedCustom
  | HookBackpressureCustom
  | UnknownCustomEvent;

export type AGUIEvent =
  | RunStartedEvent
  | RunFinishedEvent
  | RunErrorEvent
  | TextMessageStartEvent
  | TextMessageContentEvent
  | TextMessageEndEvent
  | ToolCallStartEvent
  | ToolCallArgsEvent
  | ToolCallEndEvent
  | ToolCallResultEvent
  | StateSnapshotEvent
  | CustomAGUIEvent;

export type ToolCallState = "running" | "done" | "denied";

export interface ToolCallView {
  id: string;
  name: string;
  args: string;
  state: ToolCallState;
  result?: string;
  meta?: string;
}

export interface ArtifactView {
  id: string;
  filename: string;
  size_bytes: number;
  content_type: string;
}

export interface MessageView {
  id: string;
  role: MessageRole;
  content: string;
  finalized: boolean;
  toolCalls: ToolCallView[];
  artifacts: ArtifactView[];
  created_at?: string;
  seq?: number;
}

export type ConnectionState =
  | "idle"
  | "connecting"
  | "live"
  | "replaying"
  | "offline"
  | "error";

export interface SessionViewState {
  detail: SessionDetail | null;
  messages: MessageView[];
  hasMoreHistory: boolean;
  oldestSeq: number | null;
  connection: ConnectionState;
  activeRunId: string | null;
  lastEventSeq: number | null;
  errorMessage: string | null;
}

export type ReducerAction =
  | { type: "HYDRATE_DETAIL"; detail: SessionDetail }
  | {
      type: "HYDRATE_MESSAGES";
      messages: MessageView[];
      hasMore: boolean;
      oldestSeq: number | null;
    }
  | { type: "OPTIMISTIC_USER_MESSAGE"; message: MessageView }
  | { type: "ROLLBACK_OPTIMISTIC"; messageId: string }
  | { type: "AGUI_EVENT"; event: AGUIEvent; seq: number }
  | { type: "SET_CONNECTION"; state: ConnectionState; error?: string | null }
  | { type: "RESET_RUN" };
