"use client";

import { useCallback, useEffect, useReducer, useRef } from "react";
import {
  openRunStream,
  resumeRunStream,
  StreamWindowExceededError,
} from "./agui";
import { listMessages } from "./api";
import {
  applyAction,
  INITIAL_STATE,
  persistedToMessageView,
} from "./reducer";
import type {
  AGUIEvent,
  MessageView,
  PersistedMessage,
  ReducerAction,
  SessionDetail,
  SessionViewState,
} from "./types";

const MAX_OFFLINE_BACKOFF_MS = 5_000;
const HISTORY_PAGE_SIZE = 500;

export interface UseRunLoopOptions {
  detail: SessionDetail;
  initialMessages: PersistedMessage[];
  initialHasMore: boolean;
  initialOldestSeq: number | null;
}

export interface UseRunLoopReturn {
  state: SessionViewState;
  submit: (text: string) => void;
  retry: () => void;
  dispatch: React.Dispatch<ReducerAction>;
}

function makeOptimisticAnalyst(content: string): MessageView {
  return {
    id: `local-${cryptoRandomId()}`,
    role: "user",
    content,
    finalized: true,
    toolCalls: [],
    artifacts: [],
  };
}

function cryptoRandomId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2);
}

function hydrateInitial(opts: UseRunLoopOptions): SessionViewState {
  const messages = opts.initialMessages.map(persistedToMessageView);
  return {
    ...INITIAL_STATE,
    detail: opts.detail,
    messages,
    hasMoreHistory: opts.initialHasMore,
    oldestSeq: opts.initialOldestSeq,
  };
}

export function useRunLoop(opts: UseRunLoopOptions): UseRunLoopReturn {
  const [state, dispatch] = useReducer(applyAction, opts, hydrateInitial);

  const abortRef = useRef<AbortController | null>(null);
  const lastSubmissionRef = useRef<{
    text: string;
    optimisticId: string;
  } | null>(null);
  const seqCounterRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const consumeStream = useCallback(
    async (
      stream: AsyncGenerator<AGUIEvent>,
      mode: "live" | "replay",
    ): Promise<void> => {
      let sawFirstAfterCursor = mode === "live";
      for await (const event of stream) {
        seqCounterRef.current += 1;
        const wireSeq =
          typeof event.seq === "number" ? event.seq : seqCounterRef.current;
        if (
          mode === "replay" &&
          !sawFirstAfterCursor &&
          stateRef.current.lastEventSeq !== null &&
          wireSeq > stateRef.current.lastEventSeq
        ) {
          sawFirstAfterCursor = true;
          dispatch({ type: "SET_CONNECTION", state: "live" });
        }
        dispatch({ type: "AGUI_EVENT", event, seq: seqCounterRef.current });
        if (event.type === "RUN_FINISHED" || event.type === "RUN_ERROR") {
          return;
        }
      }
    },
    [],
  );

  const fallbackToHistory = useCallback(async () => {
    if (!stateRef.current.detail) return;
    try {
      const page = await listMessages(stateRef.current.detail.id, {
        limit: HISTORY_PAGE_SIZE,
      });
      dispatch({
        type: "HYDRATE_MESSAGES",
        messages: page.messages.map(persistedToMessageView),
        hasMore: page.has_more,
        oldestSeq: page.next_cursor,
      });
      dispatch({ type: "SET_CONNECTION", state: "idle", error: null });
      dispatch({ type: "RESET_RUN" });
    } catch (e) {
      dispatch({
        type: "SET_CONNECTION",
        state: "error",
        error:
          e instanceof Error
            ? `Could not refetch history: ${e.message}`
            : "Could not refetch history",
      });
    }
  }, []);

  const startReconnectRef = useRef<(runId: string) => Promise<void>>(
    async () => {},
  );
  const scheduleReconnectRef = useRef<(runId: string) => void>(() => {});

  const scheduleReconnect = useCallback((runId: string) => {
    if (reconnectTimerRef.current !== null) return;
    const wait = Math.min(
      MAX_OFFLINE_BACKOFF_MS,
      500 + Math.floor(Math.random() * 1000),
    );
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      if (typeof navigator !== "undefined" && navigator.onLine === false) {
        scheduleReconnectRef.current(runId);
        return;
      }
      void startReconnectRef.current(runId);
    }, wait);
  }, []);

  const startReconnect = useCallback(
    async (runId: string) => {
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      dispatch({ type: "SET_CONNECTION", state: "replaying", error: null });
      try {
        await consumeStream(
          resumeRunStream({
            sessionId: stateRef.current.detail!.id,
            runId,
            lastEventId: stateRef.current.lastEventSeq,
            signal: ctrl.signal,
          }),
          "replay",
        );
      } catch (e) {
        if (e instanceof StreamWindowExceededError) {
          await fallbackToHistory();
          return;
        }
        if (ctrl.signal.aborted) return;
        dispatch({
          type: "SET_CONNECTION",
          state: "offline",
          error: e instanceof Error ? e.message : "Reconnect failed",
        });
        scheduleReconnectRef.current(runId);
      }
    },
    [consumeStream, fallbackToHistory],
  );

  useEffect(() => {
    startReconnectRef.current = startReconnect;
  }, [startReconnect]);

  useEffect(() => {
    scheduleReconnectRef.current = scheduleReconnect;
  }, [scheduleReconnect]);

  const submit = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (state.detail?.closed_at) return;
      if (state.activeRunId !== null) return;

      const optimistic = makeOptimisticAnalyst(trimmed);
      lastSubmissionRef.current = {
        text: trimmed,
        optimisticId: optimistic.id,
      };
      dispatch({ type: "OPTIMISTIC_USER_MESSAGE", message: optimistic });
      dispatch({ type: "SET_CONNECTION", state: "connecting", error: null });

      const runId = cryptoRandomId();
      const ctrl = new AbortController();
      abortRef.current = ctrl;

      (async () => {
        try {
          await consumeStream(
            openRunStream({
              sessionId: state.detail!.id,
              runId,
              messages: [{ role: "user", content: trimmed }],
              signal: ctrl.signal,
            }),
            "live",
          );
        } catch (e) {
          if (ctrl.signal.aborted) return;
          if (stateRef.current.connection === "live") {
            dispatch({ type: "SET_CONNECTION", state: "offline", error: null });
            scheduleReconnectRef.current(runId);
            return;
          }
          dispatch({
            type: "ROLLBACK_OPTIMISTIC",
            messageId: optimistic.id,
          });
          dispatch({
            type: "SET_CONNECTION",
            state: "error",
            error: e instanceof Error ? e.message : "Failed to start run",
          });
        }
      })();
    },
    [consumeStream, state.activeRunId, state.detail],
  );

  const retry = useCallback(() => {
    if (state.connection === "live" || state.connection === "connecting") {
      return;
    }
    const prior = lastSubmissionRef.current;
    if (!prior) return;
    dispatch({
      type: "ROLLBACK_OPTIMISTIC",
      messageId: prior.optimisticId,
    });
    dispatch({ type: "RESET_RUN" });
    submit(prior.text);
  }, [state.connection, submit]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      if (reconnectTimerRef.current !== null) {
        clearTimeout(reconnectTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onOnline = () => {
      if (
        stateRef.current.connection === "offline" &&
        stateRef.current.activeRunId !== null
      ) {
        void startReconnect(stateRef.current.activeRunId);
      }
    };
    window.addEventListener("online", onOnline);
    return () => window.removeEventListener("online", onOnline);
  }, [startReconnect]);

  return { state, submit, retry, dispatch };
}
