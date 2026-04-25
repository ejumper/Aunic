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
    const next = indicatorFromProgressEvent(event);
    if (!next) {
      return;
    }
    set({
      indicatorMessage: {
        text: next.text,
        kind: next.kind,
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

const TOOL_VERBS: Record<string, string> = {
  bash: "Bashing",
  read: "Reading",
  edit: "Editing",
  write: "Writing",
  grep: "Grepping",
  glob: "Globbing",
  list: "Listing",
  web_search: "Searching",
  web_fetch: "Fetching",
  note_edit: "Editing",
  note_write: "Writing",
  sleep: "Sleeping",
  task_create: "Creating task",
  task_get: "Reading task",
  task_list: "Listing tasks",
  task_update: "Updating task",
};

function indicatorFromProgressEvent(
  event: ProgressEventPayload,
): Pick<IndicatorMessage, "text" | "kind"> | null {
  if (event.kind === "file_written" || event.kind === "tool_call") {
    return null;
  }
  if (event.kind === "loop_event") {
    return indicatorFromLoopEvent(event);
  }
  const text = cleanIndicatorText(event.message, event.kind);
  return {
    text,
    kind: event.kind === "error" ? "error" : event.kind,
  };
}

function indicatorFromLoopEvent(
  event: ProgressEventPayload,
): Pick<IndicatorMessage, "text" | "kind"> {
  const loopKind =
    typeof event.details.loop_kind === "string" ? event.details.loop_kind : "loop_event";
  if (loopKind === "provider_request") {
    const label =
      typeof event.details.active_task_label === "string"
        ? event.details.active_task_label.trim()
        : "";
    return {
      text: label ? `${label}...` : "Pontificating...",
      kind: "status",
    };
  }
  if (loopKind === "provider_response") {
    const toolCalls = Array.isArray(event.details.tool_calls)
      ? event.details.tool_calls.filter((value): value is string => typeof value === "string")
      : [];
    if (toolCalls.length > 0) {
      return {
        text: `${TOOL_VERBS[toolCalls[0]] ?? toolCalls[0].replaceAll("_", " ")}...`,
        kind: "status",
      };
    }
    return {
      text: cleanIndicatorText(event.message, "Provider responded."),
      kind: "status",
    };
  }
  if (loopKind === "tool_result") {
    const status =
      typeof event.details.status === "string" ? event.details.status : "completed";
    if (status !== "completed") {
      const toolName =
        typeof event.details.tool_name === "string" ? event.details.tool_name : "tool";
      return {
        text: `${toolName} failed.`,
        kind: "error",
      };
    }
  }
  return {
    text: cleanIndicatorText(event.message, loopKind),
    kind: loopKind === "error" ? "error" : "status",
  };
}

function cleanIndicatorText(text: string, fallback: string): string {
  const trimmed = text.trim();
  return trimmed || fallback;
}
