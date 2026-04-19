import { beforeEach, describe, expect, it, vi } from "vitest";
import { useNoteEditorStore } from "./noteEditor";
import {
  useTranscriptStore,
  type TranscriptWsClient,
} from "./transcript";
import type { ClientRequestType, RequestPayload, RequestResponse } from "../ws/requests";
import type { FileSnapshotPayload, TranscriptRowPayload } from "../ws/types";

type RequestRecord = {
  type: ClientRequestType;
  payload: unknown;
};

describe("useTranscriptStore", () => {
  beforeEach(() => {
    useTranscriptStore.getState().reset();
    useNoteEditorStore.getState().reset();
  });

  it("loadFromSnapshot populates rows, revision, and initial open state", () => {
    useTranscriptStore.getState().loadFromSnapshot(
      fileSnapshot("note.md", "rev-1", [messageRow(1, "hello")]),
    );

    expect(useTranscriptStore.getState()).toMatchObject({
      path: "note.md",
      revisionId: "rev-1",
      hasTranscript: true,
      open: true,
      rows: [messageRow(1, "hello")],
    });

    useTranscriptStore.getState().loadFromSnapshot(fileSnapshot("empty.md", "rev-2", []));

    expect(useTranscriptStore.getState()).toMatchObject({
      path: "empty.md",
      hasTranscript: false,
      open: false,
      rows: [],
    });
  });

  it("applyLiveRow upserts by row_number", () => {
    useTranscriptStore.getState().loadFromSnapshot(
      fileSnapshot("note.md", "rev-1", [messageRow(1, "old")]),
    );

    useTranscriptStore.getState().applyLiveRow({
      path: "note.md",
      row: messageRow(1, "new"),
    });
    useTranscriptStore.getState().applyLiveRow({
      path: "note.md",
      row: messageRow(2, "second"),
    });

    expect(useTranscriptStore.getState().rows).toEqual([
      messageRow(1, "new"),
      messageRow(2, "second"),
    ]);
  });

  it("toolbar actions update local UI state", () => {
    const store = useTranscriptStore.getState();

    store.toggleExpand(3);
    store.setFilter("chat");
    store.toggleSort();
    store.toggleOpen();
    store.toggleMaximized();

    expect(useTranscriptStore.getState().expandedRows.has(3)).toBe(true);
    expect(useTranscriptStore.getState().filterMode).toBe("chat");
    expect(useTranscriptStore.getState().sortOrder).toBe("ascending");
    expect(useTranscriptStore.getState().open).toBe(true);
    expect(useTranscriptStore.getState().maximized).toBe(true);
  });

  it("deleteRow sends expected revision and applies returned snapshot", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      return fileSnapshot("note.md", "rev-2", []);
    });
    useTranscriptStore.getState().loadFromSnapshot(
      fileSnapshot("note.md", "rev-1", [messageRow(1, "hello")]),
    );
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "base",
      currentDoc: "unsaved",
      dirty: true,
    });

    await useTranscriptStore.getState().deleteRow(client, 1);

    expect(requests[0]).toEqual({
      type: "delete_transcript_row",
      payload: {
        path: "note.md",
        row_number: 1,
        expected_revision: "rev-1",
      },
    });
    expect(useTranscriptStore.getState()).toMatchObject({
      revisionId: "rev-2",
      rows: [],
      status: "idle",
    });
    expect(useNoteEditorStore.getState()).toMatchObject({
      revisionId: "rev-2",
      currentDoc: "unsaved",
      dirty: true,
    });
  });

  it("deleteSearchResult sends expected revision and applies returned snapshot", async () => {
    const requests: RequestRecord[] = [];
    const updated = fileSnapshot("note.md", "rev-2", [
      searchRow(2, [{ title: "Docs", url: "https://docs.example/" }]),
    ]);
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      return updated;
    });
    useTranscriptStore.getState().loadFromSnapshot(
      fileSnapshot("note.md", "rev-1", [
        searchRow(2, [
          { title: "Python", url: "https://python.example/" },
          { title: "Docs", url: "https://docs.example/" },
        ]),
      ]),
    );

    await useTranscriptStore.getState().deleteSearchResult(client, 2, 0);

    expect(requests[0]).toEqual({
      type: "delete_search_result",
      payload: {
        path: "note.md",
        row_number: 2,
        result_index: 0,
        expected_revision: "rev-1",
      },
    });
    expect(useTranscriptStore.getState().rows).toEqual(updated.transcript_rows);
  });

  it("applyFileChanged reloads transcript rows from the backend", async () => {
    const client = mockClient(async () =>
      fileSnapshot("note.md", "rev-2", [messageRow(1, "remote")]),
    );
    useTranscriptStore.getState().loadFromSnapshot(
      fileSnapshot("note.md", "rev-1", [messageRow(1, "local")]),
    );

    await useTranscriptStore.getState().applyFileChanged(client, {
      path: "note.md",
      revision_id: "rev-2",
      kind: "modified",
      exists: true,
      captured_at: "2026-04-17T00:00:00Z",
    });

    expect(useTranscriptStore.getState().rows).toEqual([messageRow(1, "remote")]);
  });
});

function mockClient(
  responder: <T extends ClientRequestType>(
    type: T,
    payload: RequestPayload<T>,
  ) => Promise<RequestResponse<T>> | RequestResponse<T>,
): TranscriptWsClient {
  return {
    request: vi.fn((type, payload) => responder(type, payload)),
  } as unknown as TranscriptWsClient;
}

function fileSnapshot(
  path: string,
  revisionId: string,
  rows: TranscriptRowPayload[],
): FileSnapshotPayload {
  return {
    path,
    revision_id: revisionId,
    content_hash: revisionId,
    mtime_ns: 1,
    size_bytes: 1,
    captured_at: "2026-04-17T00:00:00Z",
    note_content: "base",
    transcript_rows: rows,
    has_transcript: rows.length > 0,
  };
}

function messageRow(rowNumber: number, content: string): TranscriptRowPayload {
  return {
    row_number: rowNumber,
    role: "assistant",
    type: "message",
    tool_name: null,
    tool_id: null,
    content,
  };
}

function searchRow(rowNumber: number, content: unknown): TranscriptRowPayload {
  return {
    row_number: rowNumber,
    role: "tool",
    type: "tool_result",
    tool_name: "web_search",
    tool_id: "call_1",
    content,
  };
}
