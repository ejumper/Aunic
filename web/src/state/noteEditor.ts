import { create } from "zustand";
import type { WsClient, WsRequestError } from "../ws/client";
import type {
  FileChangedPayload,
  FileSnapshotPayload,
  NoteToolResultEventPayload,
} from "../ws/types";

export type NoteEditorWsClient = Pick<WsClient, "request">;

export interface NoteEditorConflict {
  reason: "save_conflict" | "model_update";
  remoteRevisionId: string;
  remoteDoc: string;
  remoteSnapshot: FileSnapshotPayload | null;
  toolName: "note_edit" | "note_write" | null;
}

export interface NoteEditorExternalReload {
  reason: "external-changed";
  snapshot: FileSnapshotPayload;
}

export interface NoteEditorSlice {
  path: string | null;
  revisionId: string | null;
  snapshot: FileSnapshotPayload | null;
  initialDoc: string;
  currentDoc: string;
  dirty: boolean;
  status: "idle" | "loading" | "saving" | "error";
  error: string | null;
  notice: string | null;
  conflict: NoteEditorConflict | null;
  externalReloadPending: NoteEditorExternalReload | null;
  documentVersion: number;
  loadForPath: (client: NoteEditorWsClient, path: string) => Promise<void>;
  markDirty: (nextDoc: string) => void;
  save: (client: NoteEditorWsClient, currentDoc: string) => Promise<boolean>;
  saveIfDirty: (client: NoteEditorWsClient, currentDoc: string) => Promise<boolean>;
  handleExternalChange: (
    client: NoteEditorWsClient,
    change: FileChangedPayload,
  ) => Promise<void>;
  handleNoteToolResult: (
    client: NoteEditorWsClient,
    event: NoteToolResultEventPayload,
  ) => Promise<void>;
  applySnapshot: (
    snapshot: FileSnapshotPayload,
    options?: {
      preserveCurrentDocIfDirty?: boolean;
      remountEditor?: boolean;
      notice?: string | null;
    },
  ) => void;
  resolveExternalReload: (strategy: "reload" | "keep") => void;
  resolveConflict: (
    client: NoteEditorWsClient,
    currentDoc: string,
    strategy: "reload" | "overwrite" | "cancel",
  ) => Promise<boolean>;
  loadConflict: (
    client: NoteEditorWsClient,
    path: string,
    options?: {
      reason?: "save_conflict" | "model_update";
      toolName?: "note_edit" | "note_write";
    },
  ) => Promise<void>;
  selectedText: string;
  setSelectedText: (text: string) => void;
  clearNotice: () => void;
  reset: () => void;
}

const EMPTY_STATE = {
  path: null,
  revisionId: null,
  snapshot: null,
  initialDoc: "",
  currentDoc: "",
  dirty: false,
  status: "idle" as const,
  error: null,
  notice: null,
  conflict: null,
  externalReloadPending: null,
  documentVersion: 0,
  selectedText: "",
};

export const useNoteEditorStore = create<NoteEditorSlice>((set, get) => ({
  ...EMPTY_STATE,

  async loadForPath(client, path) {
    set({
      path,
      revisionId: null,
      initialDoc: "",
      currentDoc: "",
      dirty: false,
      status: "loading",
      error: null,
      notice: null,
      conflict: null,
      externalReloadPending: null,
    });

    try {
      const snapshot = await client.request("read_file", { path });
      if (get().path !== path) {
        return;
      }
      set((state) => snapshotPatch(snapshot, state.documentVersion + 1));
    } catch (error) {
      if (get().path !== path) {
        return;
      }
      set({
        status: "error",
        error: formatNoteEditorError(error),
      });
    }
  },

  markDirty(nextDoc) {
    set((state) => ({
      currentDoc: nextDoc,
      dirty: nextDoc !== state.initialDoc,
      notice: state.notice === "Saved." ? null : state.notice,
    }));
  },

  async save(client, currentDoc) {
    const { path, revisionId } = get();
    if (!path || !revisionId) {
      set({ status: "error", error: "Open a file before saving." });
      return false;
    }

    set({ status: "saving", error: null, notice: null });
    try {
      const snapshot = await client.request("write_file", {
        path,
        text: currentDoc,
        expected_revision: revisionId,
      });
      if (get().path !== path) {
        return true;
      }
      set((state) => applySuccessfulSavePatch(state, snapshot, currentDoc, currentDoc, "Saved."));
      return true;
    } catch (error) {
      if (isWsRequestError(error) && error.reason === "revision_conflict") {
        await get().loadConflict(client, path);
        return false;
      }
      set({
        status: "error",
        error: formatNoteEditorError(error),
      });
      return false;
    }
  },

  async saveIfDirty(client, currentDoc) {
    if (!get().dirty) {
      return true;
    }
    return get().save(client, currentDoc);
  },

  async handleExternalChange(client, change) {
    const state = get();
    if (!state.path || change.path !== state.path) {
      return;
    }
    if (state.status === "saving") {
      return;
    }
    if (change.revision_id && change.revision_id === state.revisionId) {
      return;
    }
    if (!change.exists || change.kind === "deleted") {
      if (state.dirty) {
        set({
          status: "error",
          error: "This file was deleted on disk while browser edits are unsaved.",
        });
      } else {
        set({ ...EMPTY_STATE, documentVersion: state.documentVersion + 1 });
      }
      return;
    }

    try {
      const snapshot = await client.request("read_file", { path: change.path });
      if (get().path !== change.path) {
        return;
      }
      if (get().dirty) {
        set({
          externalReloadPending: { reason: "external-changed", snapshot },
          status: "idle",
          error: null,
        });
        return;
      }
      set((current) => ({
        ...snapshotPatch(snapshot, current.documentVersion + 1),
        notice: "Reloaded from disk.",
      }));
    } catch (error) {
      set({
        status: "error",
        error: formatNoteEditorError(error),
      });
    }
  },

  async handleNoteToolResult(client, event) {
    const state = get();
    if (!state.path || event.path !== state.path) {
      return;
    }
    if (!state.dirty && !state.externalReloadPending) {
      return;
    }

    const currentDoc = get().currentDoc;
    const baseDoc = currentDoc;
    const snapshot = event.snapshot;
    const remoteDoc = snapshot.note_content;
    const baselineDoc = typeof event.content.original_content === "string"
      ? event.content.original_content
      : null;

    if (currentDoc === remoteDoc || (baselineDoc !== null && currentDoc === baselineDoc)) {
      set((current) => ({
        ...snapshotPatch(snapshot, current.documentVersion + 1),
        notice: "Applied model update.",
      }));
      return;
    }

    if (event.tool_name === "note_edit") {
      const mergedDoc = reapplyNoteEditPayloadToNoteContent(currentDoc, event.content);
      if (mergedDoc !== null) {
        set({ status: "saving", error: null, notice: null });
        try {
          const written = await client.request("write_file", {
            path: event.path,
            text: mergedDoc,
            expected_revision: snapshot.revision_id,
          });
          if (get().path !== event.path) {
            return;
          }
          set((state) =>
            applySuccessfulSavePatch(
              state,
              written,
              baseDoc,
              mergedDoc,
              "Merged model edit into browser edits.",
            ));
          return;
        } catch (error) {
          if (isWsRequestError(error) && error.reason === "revision_conflict") {
            await get().loadConflict(client, event.path, {
              reason: "model_update",
              toolName: event.tool_name,
            });
            return;
          }
          set({
            status: "error",
            error: formatNoteEditorError(error),
          });
          return;
        }
      }
    }

    set({
      status: "idle",
      error: null,
      notice: null,
      externalReloadPending: null,
      conflict: {
        reason: "model_update",
        remoteRevisionId: snapshot.revision_id,
        remoteDoc,
        remoteSnapshot: snapshot,
        toolName: event.tool_name,
      },
    });
  },

  applySnapshot(snapshot, options = {}) {
    set((state) => {
      const preserveCurrentDoc = Boolean(
        options.preserveCurrentDocIfDirty &&
          state.dirty &&
          (state.path === null || state.path === snapshot.path),
      );
      const documentVersion = options.remountEditor === false
        ? state.documentVersion
        : state.documentVersion + 1;
      const patch = snapshotPatch(snapshot, documentVersion);
      if (!preserveCurrentDoc) {
        return {
          ...patch,
          notice: options.notice ?? state.notice,
        };
      }
      return {
        ...patch,
        currentDoc: state.currentDoc,
        dirty: state.currentDoc !== snapshot.note_content,
        documentVersion: state.documentVersion,
        notice: options.notice ?? state.notice,
      };
    });
  },

  resolveExternalReload(strategy) {
    const pending = get().externalReloadPending;
    if (!pending) {
      return;
    }
    if (strategy === "keep") {
      set({
        externalReloadPending: null,
        notice: "Keeping browser edits.",
      });
      return;
    }
    set((state) => ({
      ...snapshotPatch(pending.snapshot, state.documentVersion + 1),
      notice: "Reloaded from disk.",
    }));
  },

  async resolveConflict(client, currentDoc, strategy) {
    const conflict = get().conflict;
    const path = get().path;
    if (!conflict || !path) {
      return false;
    }

    if (strategy === "cancel") {
      set({ conflict: null, status: "idle" });
      return true;
    }

    if (strategy === "reload") {
      if (conflict.remoteSnapshot) {
        set((state) => ({
          ...snapshotPatch(conflict.remoteSnapshot as FileSnapshotPayload, state.documentVersion + 1),
          notice: conflict.reason === "model_update" ? "Applied model update." : "Reloaded their version.",
        }));
        return true;
      }
      set((state) => ({
        path,
        revisionId: conflict.remoteRevisionId,
        snapshot: state.snapshot,
        initialDoc: conflict.remoteDoc,
        currentDoc: conflict.remoteDoc,
        dirty: false,
        status: "idle",
        error: null,
        notice: "Reloaded their version.",
        conflict: null,
        externalReloadPending: null,
        documentVersion: state.documentVersion + 1,
      }));
      return true;
    }

    set({ status: "saving", error: null, notice: null });
    try {
      const snapshot = await client.request("write_file", {
        path,
        text: currentDoc,
        expected_revision: conflict.remoteRevisionId,
      });
      if (get().path !== path) {
        return true;
      }
      set((state) => applySuccessfulSavePatch(state, snapshot, currentDoc, currentDoc, "Saved."));
      return true;
    } catch (error) {
      if (isWsRequestError(error) && error.reason === "revision_conflict") {
        await get().loadConflict(client, path);
        return false;
      }
      set({
        status: "error",
        error: formatNoteEditorError(error),
      });
      return false;
    }
  },

  setSelectedText(text) {
    set({ selectedText: text });
  },

  clearNotice() {
    set({ notice: null });
  },

  reset() {
    set((state) => ({ ...EMPTY_STATE, documentVersion: state.documentVersion + 1 }));
  },

  async loadConflict(client, path, options = {}) {
    try {
      const remote = await client.request("read_file", { path });
      set({
        status: "idle",
        error: null,
        notice: null,
        externalReloadPending: null,
        conflict: {
          reason: options.reason ?? "save_conflict",
          remoteRevisionId: remote.revision_id,
          remoteDoc: remote.note_content,
          remoteSnapshot: remote,
          toolName: options.toolName ?? null,
        },
      });
    } catch (error) {
      set({
        status: "error",
        error: formatNoteEditorError(error),
      });
    }
  },
}));

function snapshotPatch(
  snapshot: FileSnapshotPayload,
  documentVersion: number,
): Partial<NoteEditorSlice> {
  return {
    path: snapshot.path,
    revisionId: snapshot.revision_id,
    snapshot,
    initialDoc: snapshot.note_content,
    currentDoc: snapshot.note_content,
    dirty: false,
    status: "idle",
    error: null,
    conflict: null,
    externalReloadPending: null,
    documentVersion,
  };
}

function applySuccessfulSavePatch(
  state: NoteEditorSlice,
  snapshot: FileSnapshotPayload,
  baseDoc: string,
  submittedDoc: string,
  notice: string,
): Partial<NoteEditorSlice> {
  const hasNewerLocalEdits = state.currentDoc !== baseDoc;
  return {
    ...snapshotPatch(snapshot, state.documentVersion),
    currentDoc: hasNewerLocalEdits ? state.currentDoc : submittedDoc,
    dirty: hasNewerLocalEdits ? state.currentDoc !== snapshot.note_content : false,
    notice,
  };
}

function reapplyNoteEditPayloadToNoteContent(
  currentNoteContent: string,
  payload: Record<string, unknown>,
): string | null {
  const actualOld = payload.actual_old_string;
  const oldString = payload.old_string;
  const newString = payload.new_string;
  const replaceAll = Boolean(payload.replace_all ?? false);
  const oldValue = typeof actualOld === "string" && actualOld
    ? actualOld
    : typeof oldString === "string" && oldString
      ? oldString
      : null;
  if (oldValue === null || typeof newString !== "string") {
    return null;
  }

  const updated = applyExactEdit(currentNoteContent, {
    oldString: oldValue,
    newString,
    replaceAll,
  });
  if (updated === null || updated === currentNoteContent) {
    return null;
  }
  return updated;
}

function applyExactEdit(
  original: string,
  {
    oldString,
    newString,
    replaceAll,
  }: {
    oldString: string;
    newString: string;
    replaceAll: boolean;
  },
): string | null {
  let candidateOld = oldString;
  let candidateNew = newString;
  let occurrences = countOccurrences(original, candidateOld);
  if (occurrences === 0 && oldString.includes("'")) {
    const smartOld = oldString.replaceAll("'", "’");
    occurrences = countOccurrences(original, smartOld);
    if (occurrences > 0) {
      candidateOld = smartOld;
      candidateNew = newString.replaceAll("'", "’");
    }
  }
  if (occurrences === 0) {
    return null;
  }
  if (!replaceAll && occurrences !== 1) {
    return null;
  }
  if (replaceAll) {
    return original.split(candidateOld).join(candidateNew);
  }
  return original.replace(candidateOld, candidateNew);
}

function countOccurrences(text: string, query: string): number {
  if (!query) {
    return 0;
  }
  return text.split(query).length - 1;
}

function isWsRequestError(error: unknown): error is WsRequestError {
  return (
    error instanceof Error &&
    "reason" in error &&
    typeof (error as WsRequestError).reason === "string"
  );
}

function formatNoteEditorError(error: unknown): string {
  if (isWsRequestError(error)) {
    return `${error.reason}${error.details ? ` ${JSON.stringify(error.details)}` : ""}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
