import { create } from "zustand";
import type { WsClient, WsRequestError } from "../ws/client";
import type { FileChangedPayload, FileSnapshotPayload } from "../ws/types";

export type NoteEditorWsClient = Pick<WsClient, "request">;

export interface NoteEditorConflict {
  remoteRevisionId: string;
  remoteDoc: string;
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
  loadConflict: (client: NoteEditorWsClient, path: string) => Promise<void>;
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
      set((state) => ({
        ...snapshotPatch(snapshot, state.documentVersion),
        dirty: snapshot.note_content !== currentDoc,
        currentDoc,
        notice: "Saved.",
      }));
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
      set((state) => ({
        ...snapshotPatch(snapshot, state.documentVersion),
        currentDoc,
        dirty: snapshot.note_content !== currentDoc,
        notice: "Saved.",
      }));
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

  async loadConflict(client, path) {
    try {
      const remote = await client.request("read_file", { path });
      set({
        status: "idle",
        error: null,
        conflict: {
          remoteRevisionId: remote.revision_id,
          remoteDoc: remote.note_content,
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
