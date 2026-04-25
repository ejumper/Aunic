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

  it("openProjectFile keeps the source anchor while changing the displayed file", () => {
    useExplorerStore.setState({
      openFile: "notes/source.md",
      sourceFile: "notes/source.md",
      selected: "notes/source.md",
    });

    useExplorerStore.getState().openProjectFile("notes/child.md", "child-node");

    expect(useExplorerStore.getState().sourceFile).toBe("notes/source.md");
    expect(useExplorerStore.getState().openFile).toBe("notes/child.md");
    expect(useExplorerStore.getState().projectSelected).toBe("child-node");
  });

  it("createProjectFile creates relative to the source file and keeps the source anchor", async () => {
    const requests: RequestRecord[] = [];
    useExplorerStore.setState({
      openFile: "notes/source.md",
      sourceFile: "notes/source.md",
      entriesByDir: {
        notes: [{ name: "source.md", kind: "file", path: "notes/source.md" }],
      },
    });
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "create_file") {
        return fileSnapshot("notes/new.md");
      }
      if (type === "add_include") {
        return {
          source_file: "notes/source.md",
          entries: [
            {
              id: "entry:notes/new.md",
              path: "notes/new.md",
              name: "new.md",
              kind: "file",
              scope: "entry",
              active: true,
              effective_active: true,
              checkable: true,
              removable: true,
              exists: true,
              openable: true,
              recursive: false,
              children: [],
            },
          ],
          plans: [],
          active_plan_id: null,
        };
      }
      return {
        path: "notes",
        entries: [
          { name: "source.md", kind: "file", path: "notes/source.md" },
          { name: "new.md", kind: "file", path: "notes/new.md" },
        ],
      };
    });

    await useExplorerStore.getState().createProjectFile(client, "new.md");

    expect(requests).toEqual([
      { type: "create_file", payload: { path: "notes/new.md" } },
      {
        type: "add_include",
        payload: {
          source_file: "notes/source.md",
          target_path: "notes/new.md",
          recursive: false,
        },
      },
      { type: "list_files", payload: { subpath: "notes" } },
    ]);
    expect(useExplorerStore.getState().sourceFile).toBe("notes/source.md");
    expect(useExplorerStore.getState().openFile).toBe("notes/new.md");
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

  it("deletePlan returns to the source file when deleting the open plan", async () => {
    const requests: RequestRecord[] = [];
    useExplorerStore.setState({
      sourceFile: "notes/source.md",
      openFile: ".aunic/plans/browser-plan.md",
      selected: ".aunic/plans/browser-plan.md",
      projectSelected: "plan:2026-04-23-browser-plan",
      projectState: {
        source_file: "notes/source.md",
        entries: [],
        plans: [
          {
            id: "plan:2026-04-23-browser-plan",
            plan_id: "2026-04-23-browser-plan",
            path: ".aunic/plans/browser-plan.md",
            name: "browser-plan.md",
            title: "Browser Plan",
            status: "draft",
            active: true,
            exists: true,
            openable: true,
          },
        ],
        active_plan_id: "2026-04-23-browser-plan",
      },
    });
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      return {
        source_file: "notes/source.md",
        entries: [],
        plans: [],
        active_plan_id: null,
      };
    });

    await useExplorerStore.getState().deletePlan(client, "2026-04-23-browser-plan");

    expect(requests).toEqual([
      {
        type: "delete_plan",
        payload: {
          source_file: "notes/source.md",
          plan_id: "2026-04-23-browser-plan",
        },
      },
    ]);
    expect(useExplorerStore.getState().sourceFile).toBe("notes/source.md");
    expect(useExplorerStore.getState().openFile).toBe("notes/source.md");
    expect(useExplorerStore.getState().selected).toBe("notes/source.md");
    expect(useExplorerStore.getState().projectSelected).toBeNull();
    expect(useExplorerStore.getState().projectState?.plans).toEqual([]);
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
