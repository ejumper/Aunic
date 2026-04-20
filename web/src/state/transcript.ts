import { create } from "zustand";
import { useNoteEditorStore } from "./noteEditor";
import type { WsClient, WsRequestError } from "../ws/client";
import type {
  FileChangedPayload,
  FileSnapshotPayload,
  TranscriptRowEventPayload,
  TranscriptRowPayload,
} from "../ws/types";

export type TranscriptFilter = "all" | "chat" | "tools" | "search";
export type TranscriptSortOrder = "ascending" | "descending";
export type TranscriptWsClient = Pick<WsClient, "request">;

export interface TranscriptSlice {
  path: string | null;
  rows: TranscriptRowPayload[];
  revisionId: string | null;
  hasTranscript: boolean;
  filterMode: TranscriptFilter;
  sortOrder: TranscriptSortOrder;
  expandedRows: Set<number>;
  open: boolean;
  maximized: boolean;
  status: "idle" | "loading" | "deleting" | "error";
  error: string | null;
  loadFromSnapshot: (snapshot: FileSnapshotPayload) => void;
  applyLiveRow: (event: TranscriptRowEventPayload) => void;
  applyFileChanged: (
    client: TranscriptWsClient,
    change: FileChangedPayload,
  ) => Promise<void>;
  toggleExpand: (rowNumber: number) => void;
  setFilter: (mode: TranscriptFilter) => void;
  toggleSort: () => void;
  setOpen: (open: boolean) => void;
  toggleOpen: () => void;
  toggleMaximized: () => void;
  deleteRow: (client: TranscriptWsClient, rowNumber: number) => Promise<void>;
  deleteSearchResult: (
    client: TranscriptWsClient,
    rowNumber: number,
    resultIndex: number,
  ) => Promise<void>;
  reset: () => void;
}

const EMPTY_STATE = {
  path: null,
  rows: [] as TranscriptRowPayload[],
  revisionId: null,
  hasTranscript: false,
  filterMode: "all" as const,
  sortOrder: "descending" as const,
  expandedRows: new Set<number>(),
  open: false,
  maximized: false,
  status: "idle" as const,
  error: null,
};

export const useTranscriptStore = create<TranscriptSlice>((set, get) => ({
  ...EMPTY_STATE,

  loadFromSnapshot(snapshot) {
    set((state) => {
      const samePath = state.path === snapshot.path;
      const rowNumbers = new Set(snapshot.transcript_rows.map((row) => row.row_number));
      return {
        path: snapshot.path,
        rows: snapshot.transcript_rows,
        revisionId: snapshot.revision_id,
        hasTranscript: snapshot.has_transcript,
        expandedRows: new Set(
          [...state.expandedRows].filter((rowNumber) => rowNumbers.has(rowNumber)),
        ),
        open: samePath ? state.open : snapshot.has_transcript,
        maximized: samePath ? state.maximized : false,
        status: "idle",
        error: null,
      };
    });
  },

  applyLiveRow(event) {
    set((state) => {
      if (state.path !== event.path) {
        return {};
      }
      const hadRows = state.rows.length > 0;
      const rows = upsertRow(state.rows, event.row);
      return {
        rows,
        hasTranscript: rows.length > 0,
        open: state.open || !hadRows,
        error: null,
      };
    });
  },

  async applyFileChanged(client, change) {
    const state = get();
    if (!state.path || change.path !== state.path) {
      return;
    }
    if (change.revision_id && change.revision_id === state.revisionId) {
      return;
    }
    if (!change.exists || change.kind === "deleted") {
      set({ ...EMPTY_STATE, expandedRows: new Set() });
      return;
    }

    set({ status: "loading", error: null });
    try {
      const snapshot = await client.request("read_file", { path: change.path });
      if (get().path !== change.path) {
        return;
      }
      get().loadFromSnapshot(snapshot);
    } catch (error) {
      set({ status: "error", error: formatTranscriptError(error) });
    }
  },

  toggleExpand(rowNumber) {
    set((state) => {
      const expandedRows = new Set(state.expandedRows);
      if (expandedRows.has(rowNumber)) {
        expandedRows.delete(rowNumber);
      } else {
        expandedRows.add(rowNumber);
      }
      return { expandedRows };
    });
  },

  setFilter(mode) {
    set({ filterMode: mode });
  },

  toggleSort() {
    set((state) => ({
      sortOrder: state.sortOrder === "descending" ? "ascending" : "descending",
    }));
  },

  setOpen(open) {
    set((state) => ({
      open,
      maximized: open ? state.maximized : false,
    }));
  },

  toggleOpen() {
    set((state) => ({
      open: !state.open,
      maximized: state.open ? false : state.maximized,
    }));
  },

  toggleMaximized() {
    set((state) => ({
      maximized: state.open ? !state.maximized : true,
      open: true,
    }));
  },

  async deleteRow(client, rowNumber) {
    const { path, revisionId } = get();
    if (!path || !revisionId) {
      set({ status: "error", error: "Open a file before deleting transcript rows." });
      return;
    }

    set({ status: "deleting", error: null });
    try {
      const snapshot = await client.request("delete_transcript_row", {
        path,
        row_number: rowNumber,
        expected_revision: revisionId,
      });
      get().loadFromSnapshot(snapshot);
      applySnapshotToNoteEditor(snapshot);
    } catch (error) {
      set({ status: "error", error: formatTranscriptError(error) });
    }
  },

  async deleteSearchResult(client, rowNumber, resultIndex) {
    const { path, revisionId } = get();
    if (!path || !revisionId) {
      set({ status: "error", error: "Open a file before deleting search results." });
      return;
    }

    set({ status: "deleting", error: null });
    try {
      const snapshot = await client.request("delete_search_result", {
        path,
        row_number: rowNumber,
        result_index: resultIndex,
        expected_revision: revisionId,
      });
      get().loadFromSnapshot(snapshot);
      applySnapshotToNoteEditor(snapshot);
    } catch (error) {
      set({ status: "error", error: formatTranscriptError(error) });
    }
  },

  reset() {
    set({ ...EMPTY_STATE, expandedRows: new Set() });
  },
}));

function upsertRow(
  rows: TranscriptRowPayload[],
  row: TranscriptRowPayload,
): TranscriptRowPayload[] {
  return [...rows.filter((item) => item.row_number !== row.row_number), row].sort(
    (left, right) => left.row_number - right.row_number,
  );
}

function applySnapshotToNoteEditor(snapshot: FileSnapshotPayload): void {
  useNoteEditorStore.getState().applySnapshot(snapshot, {
    preserveCurrentDocIfDirty: true,
    remountEditor: false,
  });
}

function isWsRequestError(error: unknown): error is WsRequestError {
  return (
    error instanceof Error &&
    "reason" in error &&
    typeof (error as WsRequestError).reason === "string"
  );
}

function formatTranscriptError(error: unknown): string {
  if (isWsRequestError(error)) {
    return `${error.reason}${error.details ? ` ${JSON.stringify(error.details)}` : ""}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
