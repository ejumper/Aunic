import {
  createContext,
  type MutableRefObject,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  getOrCreateBrowserInstanceId,
  getCurrentBrowserPageId,
  rotateBrowserInstanceId,
} from "../browserSession";
import { wsUrl } from "../env";
import { ROOT_DIR, useExplorerStore } from "../state/explorer";
import { useNoteEditorStore } from "../state/noteEditor";
import { useSessionStore } from "../state/session";
import { useTranscriptStore } from "../state/transcript";
import {
  WsClient,
  type ConnectionState,
  type WsDiagnostics,
  type WsRequestError,
} from "./client";
import type {
  FileChangedPayload,
  NoteToolResultEventPayload,
  PendingPermissionPayload,
  ProgressEventPayload,
  SessionStatePayload,
  TranscriptRowEventPayload,
} from "./types";

interface WsContextValue {
  client: WsClient;
  state: ConnectionState;
  lastConnectedAt: Date | null;
  diagnostics: WsDiagnostics | null;
}

const WsContext = createContext<WsContextValue | null>(null);

interface WsProviderProps {
  children: ReactNode;
  url?: string;
}

export function WsProvider({ children, url = wsUrl }: WsProviderProps) {
  const [state, setState] = useState<ConnectionState>("idle");
  const [lastConnectedAt, setLastConnectedAt] = useState<Date | null>(null);
  const [diagnostics, setDiagnostics] = useState<WsDiagnostics | null>(null);
  const previousConnectionStateRef = useRef<ConnectionState>("idle");
  const setSession = useSessionStore((store) => store.setSession);
  const applyProgressEvent = useSessionStore((store) => store.applyProgressEvent);
  const setPendingPermission = useSessionStore((store) => store.setPendingPermission);
  const setIndicatorMessage = useSessionStore((store) => store.setIndicatorMessage);
  const clearSession = useSessionStore((store) => store.clearSession);
  const instanceIdRef = useRef<string>(getOrCreateBrowserInstanceId());
  const pageIdRef = useRef<string>(getCurrentBrowserPageId());
  const helloAttemptRef = useRef(0);

  const client = useMemo(
    () =>
      new WsClient({
        url,
        autoHelloOnOpen: false,
        onStateChange: (nextState) => {
          const previousState = previousConnectionStateRef.current;
          previousConnectionStateRef.current = nextState;
          setState(nextState);
          if (nextState === "open") {
            setLastConnectedAt(new Date());
          }
          if (nextState === "reconnecting" && previousState !== "reconnecting") {
            setIndicatorMessage("Browser connection lost. Reconnecting...", "error");
          } else if (nextState === "closed" && previousState !== "closed") {
            setIndicatorMessage("Browser connection closed.", "error");
          } else if (
            nextState === "open" &&
            (previousState === "reconnecting" || previousState === "closed")
          ) {
            setIndicatorMessage("Browser connection restored.");
          }
        },
        onDiagnostics: setDiagnostics,
      }),
    [setIndicatorMessage, url],
  );

  useEffect(() => {
    if (state !== "open") {
      return;
    }
    const attemptId = ++helloAttemptRef.current;
    void performHelloHandshake({
      client,
      attemptId,
      instanceIdRef,
      pageIdRef,
      helloAttemptRef,
      setIndicatorMessage,
      clearSession,
    });
  }, [state, client, clearSession, setIndicatorMessage]);

  useEffect(() => {
    const unsubscribe = client.on("session_state", (session: SessionStatePayload) => {
      setSession(session);
      const explorer = useExplorerStore.getState();
      if (explorer.entriesByDir[ROOT_DIR] === undefined) {
        void explorer.loadDir(client, ROOT_DIR);
      }
    });
    const unsubscribeFileChanged = client.on("file_changed", (event: FileChangedPayload) => {
      useExplorerStore.getState().handleFileChanged(client, event);
      void (async () => {
        await useNoteEditorStore.getState().handleExternalChange(client, event);
        const noteState = useNoteEditorStore.getState();
        const freshSnapshot =
          noteState.snapshot?.path === event.path &&
          (!event.revision_id || noteState.snapshot.revision_id === event.revision_id)
            ? noteState.snapshot
            : noteState.externalReloadPending?.snapshot.path === event.path &&
                (!event.revision_id ||
                  noteState.externalReloadPending.snapshot.revision_id === event.revision_id)
              ? noteState.externalReloadPending.snapshot
              : null;
        if (freshSnapshot) {
          useTranscriptStore.getState().loadFromSnapshot(freshSnapshot);
          return;
        }
        await useTranscriptStore.getState().applyFileChanged(client, event);
      })();
    });
    const unsubscribeTranscriptRow = client.on(
      "transcript_row",
      (event: TranscriptRowEventPayload) => {
        useTranscriptStore.getState().applyLiveRow(event);
      },
    );
    const unsubscribeNoteToolResult = client.on(
      "note_tool_result",
      (event: NoteToolResultEventPayload) => {
        void useNoteEditorStore.getState().handleNoteToolResult(client, event);
      },
    );
    const unsubscribeProgressEvent = client.on(
      "progress_event",
      (event: ProgressEventPayload) => {
        applyProgressEvent(event);
      },
    );
    const unsubscribePermissionRequest = client.on(
      "permission_request",
      (event: PendingPermissionPayload) => {
        setPendingPermission(event);
      },
    );
    const unsubscribeError = client.on("error", (event) => {
      if (event.reason === "instance_conflict") {
        return;
      }
      setIndicatorMessage(
        `${event.reason}${event.details ? ` ${JSON.stringify(event.details)}` : ""}`,
        "error",
      );
    });
    client.start();
    return () => {
      unsubscribe();
      unsubscribeFileChanged();
      unsubscribeTranscriptRow();
      unsubscribeNoteToolResult();
      unsubscribeProgressEvent();
      unsubscribePermissionRequest();
      unsubscribeError();
      client.stop();
      clearSession();
      useExplorerStore.getState().reset();
      useNoteEditorStore.getState().reset();
      useTranscriptStore.getState().reset();
    };
  }, [applyProgressEvent, clearSession, client, setIndicatorMessage, setPendingPermission, setSession]);

  return (
    <WsContext.Provider value={{ client, state, lastConnectedAt, diagnostics }}>
      {children}
    </WsContext.Provider>
  );
}

async function performHelloHandshake({
  client,
  attemptId,
  instanceIdRef,
  pageIdRef,
  helloAttemptRef,
  setIndicatorMessage,
  clearSession,
}: {
  client: WsClient;
  attemptId: number;
  instanceIdRef: MutableRefObject<string>;
  pageIdRef: MutableRefObject<string>;
  helloAttemptRef: MutableRefObject<number>;
  setIndicatorMessage: (text: string, kind?: string) => void;
  clearSession: () => void;
}): Promise<void> {
  for (let retry = 0; retry < 5; retry += 1) {
    try {
      await client.request("hello", {
        instance_id: instanceIdRef.current,
        page_id: pageIdRef.current,
      });
      return;
    } catch (error) {
      if (attemptId !== helloAttemptRef.current) {
        return;
      }
      if (!isWsRequestErrorLike(error) || error.reason !== "instance_conflict") {
        if (isWsRequestErrorLike(error) && error.reason === "not_connected") {
          return;
        }
        setIndicatorMessage(formatHelloError(error), "error");
        return;
      }
      await sleep(100);
      if (client.state !== "open") {
        return;
      }
    }
  }

  instanceIdRef.current = rotateBrowserInstanceId();
  clearSession();
  useExplorerStore.getState().reset();
  useNoteEditorStore.getState().reset();
  useTranscriptStore.getState().reset();
  setIndicatorMessage("Opened a fresh Aunic instance for this browser tab.");
  client.stop();
  client.start();
}

function isWsRequestErrorLike(error: unknown): error is WsRequestError {
  return Boolean(error && typeof error === "object" && "reason" in error);
}

function formatHelloError(error: unknown): string {
  if (isWsRequestErrorLike(error)) {
    return error.reason;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

export function useWs(): WsContextValue {
  const context = useContext(WsContext);
  if (context === null) {
    throw new Error("useWs must be used inside WsProvider");
  }
  return context;
}

export function useConnectionState(): Pick<
  WsContextValue,
  "state" | "lastConnectedAt" | "diagnostics"
> {
  const { state, lastConnectedAt, diagnostics } = useWs();
  return { state, lastConnectedAt, diagnostics };
}
