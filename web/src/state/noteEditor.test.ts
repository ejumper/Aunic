import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  useNoteEditorStore,
  type NoteEditorWsClient,
} from "./noteEditor";
import type { ClientRequestType, RequestPayload, RequestResponse } from "../ws/requests";
import type { FileSnapshotPayload, NoteToolResultEventPayload } from "../ws/types";
import type { WsRequestError } from "../ws/client";

type RequestRecord = {
  type: ClientRequestType;
  payload: unknown;
};

describe("useNoteEditorStore", () => {
  beforeEach(() => {
    useNoteEditorStore.getState().reset();
  });

  it("loadForPath populates initial document and revision", async () => {
    const client = mockClient(async () => fileSnapshot("note.md", "hello", "rev-1"));

    await useNoteEditorStore.getState().loadForPath(client, "note.md");

    expect(useNoteEditorStore.getState()).toMatchObject({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "hello",
      currentDoc: "hello",
      dirty: false,
      status: "idle",
      conflict: null,
    });
  });

  it("markDirty tracks divergence from the loaded document", async () => {
    const client = mockClient(async () => fileSnapshot("note.md", "hello", "rev-1"));
    await useNoteEditorStore.getState().loadForPath(client, "note.md");

    useNoteEditorStore.getState().markDirty("changed");
    expect(useNoteEditorStore.getState().dirty).toBe(true);

    useNoteEditorStore.getState().markDirty("hello");
    expect(useNoteEditorStore.getState().dirty).toBe(false);
  });

  it("save sends expected_revision and updates state on success", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "read_file") {
        return fileSnapshot("note.md", "hello", "rev-1");
      }
      return fileSnapshot("note.md", "changed", "rev-2");
    });
    await useNoteEditorStore.getState().loadForPath(client, "note.md");
    useNoteEditorStore.getState().markDirty("changed");

    await expect(useNoteEditorStore.getState().save(client, "changed")).resolves.toBe(true);

    expect(requests.at(-1)).toEqual({
      type: "write_file",
      payload: {
        path: "note.md",
        text: "changed",
        expected_revision: "rev-1",
      },
    });
    expect(useNoteEditorStore.getState()).toMatchObject({
      revisionId: "rev-2",
      initialDoc: "changed",
      dirty: false,
      notice: "Saved.",
    });
  });

  it("save fetches remote content and sets conflict on revision_conflict", async () => {
    const client = mockClient(async (type) => {
      if (type === "read_file") {
        const alreadyLoaded = useNoteEditorStore.getState().revisionId !== null;
        return alreadyLoaded
          ? fileSnapshot("note.md", "remote", "rev-remote")
          : fileSnapshot("note.md", "hello", "rev-1");
      }
      throw wsError("revision_conflict");
    });
    await useNoteEditorStore.getState().loadForPath(client, "note.md");

    await expect(useNoteEditorStore.getState().save(client, "mine")).resolves.toBe(false);

    expect(useNoteEditorStore.getState().conflict).toEqual({
      reason: "save_conflict",
      remoteRevisionId: "rev-remote",
      remoteDoc: "remote",
      remoteSnapshot: fileSnapshot("note.md", "remote", "rev-remote"),
      toolName: null,
    });
  });

  it("saveIfDirty short-circuits when clean", async () => {
    const request = vi.fn();
    const client = { request } as unknown as NoteEditorWsClient;

    await expect(useNoteEditorStore.getState().saveIfDirty(client, "same")).resolves.toBe(true);

    expect(request).not.toHaveBeenCalled();
  });

  it("handleExternalChange no-ops on matching revision echo", async () => {
    const request = vi.fn();
    const client = mockClient(async () => fileSnapshot("note.md", "hello", "rev-1"));
    await useNoteEditorStore.getState().loadForPath(client, "note.md");
    request.mockClear();

    await useNoteEditorStore.getState().handleExternalChange({ request } as unknown as NoteEditorWsClient, {
      path: "note.md",
      revision_id: "rev-1",
      kind: "modified",
      exists: true,
      captured_at: "2026-04-17T00:00:00Z",
    });

    expect(request).not.toHaveBeenCalled();
  });

  it("handleExternalChange silently swaps when clean", async () => {
    const client = mockClient(async (type) =>
      type === "read_file"
        ? fileSnapshot("note.md", "remote", "rev-2")
        : fileSnapshot("note.md", "ignored", "rev-x"),
    );
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "hello",
      currentDoc: "hello",
      dirty: false,
    });

    await useNoteEditorStore.getState().handleExternalChange(client, {
      path: "note.md",
      revision_id: "rev-2",
      kind: "modified",
      exists: true,
      captured_at: "2026-04-17T00:00:00Z",
    });

    expect(useNoteEditorStore.getState()).toMatchObject({
      initialDoc: "remote",
      currentDoc: "remote",
      revisionId: "rev-2",
      dirty: false,
      notice: "Reloaded from disk.",
    });
  });

  it("handleExternalChange sets reload banner when dirty", async () => {
    const client = mockClient(async () => fileSnapshot("note.md", "remote", "rev-2"));
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "hello",
      currentDoc: "mine",
      dirty: true,
    });

    await useNoteEditorStore.getState().handleExternalChange(client, {
      path: "note.md",
      revision_id: "rev-2",
      kind: "modified",
      exists: true,
      captured_at: "2026-04-17T00:00:00Z",
    });

    expect(useNoteEditorStore.getState().externalReloadPending?.snapshot.note_content).toBe(
      "remote",
    );
  });

  it("handleNoteToolResult merges note_edit into dirty browser edits", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "write_file") {
        return fileSnapshot("note.md", "alpha\nBETA\ndelta\n", "rev-merged");
      }
      throw new Error(`Unexpected request: ${type}`);
    });
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "alpha\nbeta\ngamma\n",
      currentDoc: "alpha\nbeta\ndelta\n",
      dirty: true,
    });

    await useNoteEditorStore.getState().handleNoteToolResult(client, noteToolResultEvent({
      tool_name: "note_edit",
      snapshot: fileSnapshot("note.md", "alpha\nBETA\ngamma\n", "rev-model"),
      content: {
        type: "note_content_edit",
        old_string: "beta",
        new_string: "BETA",
        actual_old_string: "beta",
        replace_all: false,
        original_content: "alpha\nbeta\ngamma\n",
      },
    }));

    expect(requests).toEqual([
      {
        type: "write_file",
        payload: {
          path: "note.md",
          text: "alpha\nBETA\ndelta\n",
          expected_revision: "rev-model",
        },
      },
    ]);
    expect(useNoteEditorStore.getState()).toMatchObject({
      revisionId: "rev-merged",
      initialDoc: "alpha\nBETA\ndelta\n",
      currentDoc: "alpha\nBETA\ndelta\n",
      dirty: false,
      notice: "Merged model edit into browser edits.",
      conflict: null,
      externalReloadPending: null,
    });
  });

  it("handleNoteToolResult falls back to a model-update conflict when note_write cannot merge", async () => {
    const request = vi.fn();
    const client = { request } as unknown as NoteEditorWsClient;
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "alpha\nbeta\ngamma\n",
      currentDoc: "user rewrite\n",
      dirty: true,
    });

    await useNoteEditorStore.getState().handleNoteToolResult(client, noteToolResultEvent({
      tool_name: "note_write",
      snapshot: fileSnapshot("note.md", "model rewrite\n", "rev-model"),
      content: {
        type: "note_content_write",
        content: "model rewrite\n",
        original_content: "alpha\nbeta\ngamma\n",
      },
    }));

    expect(request).not.toHaveBeenCalled();
    expect(useNoteEditorStore.getState().conflict).toEqual({
      reason: "model_update",
      remoteRevisionId: "rev-model",
      remoteDoc: "model rewrite\n",
      remoteSnapshot: fileSnapshot("note.md", "model rewrite\n", "rev-model"),
      toolName: "note_write",
    });
  });

  it("resolveExternalReload reloads the remote snapshot", () => {
    useNoteEditorStore.setState({
      externalReloadPending: {
        reason: "external-changed",
        snapshot: fileSnapshot("note.md", "remote", "rev-2"),
      },
    });

    useNoteEditorStore.getState().resolveExternalReload("reload");

    expect(useNoteEditorStore.getState()).toMatchObject({
      initialDoc: "remote",
      currentDoc: "remote",
      revisionId: "rev-2",
      dirty: false,
      externalReloadPending: null,
    });
  });

  it("resolveConflict overwrite retries with the remote revision", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      return fileSnapshot("note.md", "mine", "rev-3");
    });
    useNoteEditorStore.setState({
      path: "note.md",
      conflict: {
        reason: "save_conflict",
        remoteRevisionId: "rev-2",
        remoteDoc: "remote",
        remoteSnapshot: fileSnapshot("note.md", "remote", "rev-2"),
        toolName: null,
      },
    });

    await expect(
      useNoteEditorStore.getState().resolveConflict(client, "mine", "overwrite"),
    ).resolves.toBe(true);

    expect(requests[0]).toEqual({
      type: "write_file",
      payload: {
        path: "note.md",
        text: "mine",
        expected_revision: "rev-2",
      },
    });
    expect(useNoteEditorStore.getState().conflict).toBeNull();
  });
});

function mockClient(
  responder: <T extends ClientRequestType>(
    type: T,
    payload: RequestPayload<T>,
  ) => Promise<RequestResponse<T>> | RequestResponse<T>,
): NoteEditorWsClient {
  return {
    request: vi.fn((type, payload) => responder(type, payload)),
  } as unknown as NoteEditorWsClient;
}

function fileSnapshot(
  path: string,
  noteContent: string,
  revisionId: string,
): FileSnapshotPayload {
  return {
    path,
    revision_id: revisionId,
    content_hash: revisionId,
    mtime_ns: 1,
    size_bytes: noteContent.length,
    captured_at: "2026-04-17T00:00:00Z",
    note_content: noteContent,
    transcript_rows: [],
    has_transcript: false,
  };
}

function noteToolResultEvent(
  payload: Partial<NoteToolResultEventPayload> & Pick<NoteToolResultEventPayload, "tool_name" | "snapshot">,
): NoteToolResultEventPayload {
  return {
    path: "note.md",
    content: {},
    ...payload,
  };
}

function wsError(reason: string): WsRequestError {
  const error = new Error(reason) as WsRequestError;
  error.name = "WsRequestError";
  error.reason = reason;
  return error;
}
