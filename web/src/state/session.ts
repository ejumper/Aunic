import { create } from "zustand";
import type {
  PendingPermissionPayload,
  ProgressEventPayload,
  SessionStatePayload,
} from "../ws/types";

export interface IndicatorMessage {
  text: string;
  kind: string;
  at: Date;
}

interface SessionSlice {
  session: SessionStatePayload | null;
  runActive: boolean;
  currentRunId: string | null;
  pendingPermission: PendingPermissionPayload | null;
  indicatorMessage: IndicatorMessage | null;
  setSession: (session: SessionStatePayload) => void;
  setPendingPermission: (permission: PendingPermissionPayload | null) => void;
  clearPendingPermission: () => void;
  setIndicatorMessage: (text: string, kind?: string) => void;
  applyProgressEvent: (event: ProgressEventPayload) => void;
  clearSession: () => void;
}

export const useSessionStore = create<SessionSlice>((set) => ({
  session: null,
  runActive: false,
  currentRunId: null,
  pendingPermission: null,
  indicatorMessage: null,
  setSession: (session) =>
    set({
      session,
      runActive: session.run_active,
      currentRunId: session.run_id,
      pendingPermission: session.pending_permission,
    }),
  setPendingPermission: (permission) => set({ pendingPermission: permission }),
  clearPendingPermission: () => set({ pendingPermission: null }),
  setIndicatorMessage: (text, kind = "status") =>
    set({
      indicatorMessage: {
        text,
        kind,
        at: new Date(),
      },
    }),
  applyProgressEvent: (event) => {
    if (event.kind === "file_written" || event.kind === "tool_call") {
      return;
    }
    const text = event.message.trim() || event.kind;
    set({
      indicatorMessage: {
        text,
        kind: event.kind,
        at: new Date(),
      },
    });
  },
  clearSession: () =>
    set({
      session: null,
      runActive: false,
      currentRunId: null,
      pendingPermission: null,
      indicatorMessage: null,
    }),
}));
