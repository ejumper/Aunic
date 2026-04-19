import { beforeEach, describe, expect, it, vi } from "vitest";
import { useExplorerStore, type ExplorerWsClient } from "./explorer";
import type { ClientRequestType, RequestPayload, RequestResponse } from "../ws/requests";

type RequestRecord = {
  type: ClientRequestType;
  payload: unknown;
};

describe("useExplorerStore", () => {
  beforeEach(() => {
    useExplorerStore.getState().reset();
  });

  it("loadDir stores entries and clears loading state", async () => {
    const client = mockClient(async (type) => {
      expect(type).toBe("list_files");
      return {
        path: ".",
        entries: [{ name: "note.md", kind: "file", path: "note.md" }],
      };
    });

    await useExplorerStore.getState().loadDir(client, "");

    expect(useExplorerStore.getState().entriesByDir[""]).toEqual([
      { name: "note.md", kind: "file", path: "note.md" },
    ]);
    expect(useExplorerStore.getState().loading.size).toBe(0);
    expect(useExplorerStore.getState().error).toEqual({});
  });

  it("toggleExpand expands, lazy-loads, then collapses", async () => {
    const client = mockClient(async () => ({
      path: "notes",
      entries: [{ name: "a.md", kind: "file", path: "notes/a.md" }],
    }));

    await useExplorerStore.getState().toggleExpand(client, "notes");

    expect(useExplorerStore.getState().expanded.has("notes")).toBe(true);
    expect(useExplorerStore.getState().entriesByDir.notes).toHaveLength(1);

    await useExplorerStore.getState().toggleExpand(client, "notes");

    expect(useExplorerStore.getState().expanded.has("notes")).toBe(false);
  });

  it("createFile calls the backend, opens the file, and refreshes the parent", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "create_file") {
        return fileSnapshot("notes/new.md");
      }
      return {
        path: "notes",
        entries: [{ name: "new.md", kind: "file", path: "notes/new.md" }],
      };
    });

    await useExplorerStore.getState().createFile(client, "notes", "new.md");

    expect(requests).toEqual([
      { type: "create_file", payload: { path: "notes/new.md" } },
      { type: "list_files", payload: { subpath: "notes" } },
    ]);
    expect(useExplorerStore.getState().openFile).toBe("notes/new.md");
    expect(useExplorerStore.getState().selected).toBe("notes/new.md");
  });

  it("createFile rejects non-markdown names before calling the backend", async () => {
    const request = vi.fn();
    const client = { request } as unknown as ExplorerWsClient;

    await expect(
      useExplorerStore.getState().createFile(client, "", "new.txt"),
    ).rejects.toThrow("New files must end with .md.");

    expect(request).not.toHaveBeenCalled();
  });

  it("deleteEntry removes cached descendants after backend ack", async () => {
    useExplorerStore.setState({
      entriesByDir: {
        "": [{ name: "notes", kind: "dir", path: "notes" }],
        notes: [{ name: "old.md", kind: "file", path: "notes/old.md" }],
      },
      expanded: new Set(["notes"]),
      selected: "notes/old.md",
      openFile: "notes/old.md",
    });
    const client = mockClient(async (type) => {
      if (type === "delete_entry") {
        return { path: "notes", kind: "dir" };
      }
      return { path: ".", entries: [] };
    });

    await useExplorerStore.getState().deleteEntry(client, "notes");

    expect(useExplorerStore.getState().entriesByDir).toEqual({ "": [] });
    expect(useExplorerStore.getState().expanded.size).toBe(0);
    expect(useExplorerStore.getState().selected).toBeNull();
    expect(useExplorerStore.getState().openFile).toBeNull();
  });

  it("handleFileChanged refreshes cached parent directories", async () => {
    useExplorerStore.setState({ entriesByDir: { notes: [] } });
    const client = mockClient(async () => ({
      path: "notes",
      entries: [{ name: "changed.md", kind: "file", path: "notes/changed.md" }],
    }));

    useExplorerStore.getState().handleFileChanged(client, {
      path: "notes/changed.md",
      revision_id: "rev-1",
      kind: "created",
      exists: true,
      captured_at: "2026-04-17T00:00:00Z",
    });
    await flushPromises();

    expect(useExplorerStore.getState().entriesByDir.notes).toEqual([
      { name: "changed.md", kind: "file", path: "notes/changed.md" },
    ]);
  });
});

function mockClient(
  responder: <T extends ClientRequestType>(
    type: T,
    payload: RequestPayload<T>,
  ) => Promise<RequestResponse<T>> | RequestResponse<T>,
): ExplorerWsClient {
  return {
    request: vi.fn((type, payload) => responder(type, payload)),
  } as unknown as ExplorerWsClient;
}

function fileSnapshot(path: string) {
  return {
    path,
    revision_id: "rev-1",
    content_hash: "hash",
    mtime_ns: 1,
    size_bytes: 0,
    captured_at: "2026-04-17T00:00:00Z",
    note_content: "",
    transcript_rows: [],
    has_transcript: false,
  };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
