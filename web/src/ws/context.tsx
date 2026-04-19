import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { wsUrl } from "../env";
import { ROOT_DIR, useExplorerStore } from "../state/explorer";
import { useNoteEditorStore } from "../state/noteEditor";
import { useSessionStore } from "../state/session";
import { useTranscriptStore } from "../state/transcript";
import { WsClient, type ConnectionState } from "./client";
import type {
  FileChangedPayload,
  PendingPermissionPayload,
  ProgressEventPayload,
  SessionStatePayload,
  TranscriptRowEventPayload,
} from "./types";

interface WsContextValue {
  client: WsClient;
  state: ConnectionState;
  lastConnectedAt: Date | null;
}

const WsContext = createContext<WsContextValue | null>(null);

interface WsProviderProps {
  children: ReactNode;
  url?: string;
}

export function WsProvider({ children, url = wsUrl }: WsProviderProps) {
  const [state, setState] = useState<ConnectionState>("idle");
  const [lastConnectedAt, setLastConnectedAt] = useState<Date | null>(null);
  const setSession = useSessionStore((store) => store.setSession);
  const applyProgressEvent = useSessionStore((store) => store.applyProgressEvent);
  const setPendingPermission = useSessionStore((store) => store.setPendingPermission);
  const clearSession = useSessionStore((store) => store.clearSession);

  const client = useMemo(
    () =>
      new WsClient({
        url,
        onStateChange: (nextState) => {
          setState(nextState);
          if (nextState === "open") {
            setLastConnectedAt(new Date());
          }
        },
      }),
    [url],
  );

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
    client.start();
    return () => {
      unsubscribe();
      unsubscribeFileChanged();
      unsubscribeTranscriptRow();
      unsubscribeProgressEvent();
      unsubscribePermissionRequest();
      client.stop();
      clearSession();
      useExplorerStore.getState().reset();
      useNoteEditorStore.getState().reset();
      useTranscriptStore.getState().reset();
    };
  }, [applyProgressEvent, clearSession, client, setPendingPermission, setSession]);

  return (
    <WsContext.Provider value={{ client, state, lastConnectedAt }}>
      {children}
    </WsContext.Provider>
  );
}

export function useWs(): WsContextValue {
  const context = useContext(WsContext);
  if (context === null) {
    throw new Error("useWs must be used inside WsProvider");
  }
  return context;
}

export function useConnectionState(): Pick<WsContextValue, "state" | "lastConnectedAt"> {
  const { state, lastConnectedAt } = useWs();
  return { state, lastConnectedAt };
}
