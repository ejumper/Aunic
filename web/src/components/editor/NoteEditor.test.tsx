import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExplorerStore } from "../../state/explorer";
import { useFindStore } from "../../state/find";
import { useNoteEditorStore } from "../../state/noteEditor";
import { useSessionStore } from "../../state/session";
import type { FileSnapshotPayload } from "../../ws/types";
import { NoteEditor } from "./NoteEditor";

const { request, client } = vi.hoisted(() => {
  const request = vi.fn();
  return { request, client: { request } };
});

vi.mock("../../ws/context", () => ({
  useWs: () => ({
    client,
  }),
  useConnectionState: () => ({
    state: "open",
    lastConnectedAt: new Date("2026-04-17T12:00:00Z"),
  }),
}));

describe("NoteEditor", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    request.mockReset();
    useExplorerStore.getState().reset();
    useFindStore.getState().reset();
    useNoteEditorStore.getState().reset();
    useSessionStore.getState().clearSession();
    Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
    Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
    window.requestAnimationFrame = (callback: FrameRequestCallback) => {
      return window.setTimeout(() => callback(performance.now()), 0);
    };
    window.cancelAnimationFrame = (id: number) => window.clearTimeout(id);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    vi.useRealTimers();
    container.remove();
  });

  it("renders empty state when no file is open", async () => {
    await act(async () => {
      root.render(<NoteEditor />);
    });

    expect(container.textContent).toContain("Select a markdown file from the explorer.");
  });

  it("loads the open file and mounts CodeMirror with note content", async () => {
    request.mockResolvedValue(fileSnapshot("note.md", "# Hello\nBody", "rev-1"));
    useExplorerStore.setState({ openFile: "note.md" });

    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("read_file", { path: "note.md" });
    expect(container.querySelector(".cm-content")?.textContent).toContain("Hello");
  });

  it("renders markdown tables without dropping following content", async () => {
    request.mockResolvedValue(
      fileSnapshot(
        "note.md",
        [
          "### NDP Messages",
          "| Message | Type | Purpose |",
          "| :------ | :--- | :------ |",
          "| **Router Solicitation** | 133 | Host asks *routers* for RA |",
          "| ***Router Advertisement*** | 134 | Router shares prefix info |",
          "",
          "# Relay Agent",
          "- `ipv6 dhcp relay destination ipv6-address`",
        ].join("\n"),
        "rev-1",
      ),
    );
    useExplorerStore.setState({ openFile: "note.md" });

    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    expect(container.querySelector(".cm-aunic-md-table")).not.toBeNull();
    expect(container.querySelector(".cm-content")?.textContent).toContain("Router Solicitation");
    expect(container.querySelector(".cm-content")?.textContent).toContain("Relay Agent");
    expect(container.querySelector(".cm-aunic-md-table strong")?.textContent).toContain(
      "Router Solicitation",
    );
    expect(container.querySelector(".cm-aunic-md-table em")?.textContent).toContain("routers");
  });

  it("renders inactive markdown rules and code without raw fence markers", async () => {
    request.mockResolvedValue(
      fileSnapshot(
        "note.md",
        [
          "# Heading",
          "",
          "---",
          "",
          "Use `show ipv6 route` here.",
          "",
          "```",
          "interface Gi0/1",
          "```",
        ].join("\n"),
        "rev-1",
      ),
    );
    useExplorerStore.setState({ openFile: "note.md" });

    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    expect(container.querySelector(".cm-aunic-page-break")).not.toBeNull();
    expect(container.querySelector(".cm-aunic-inline-code")?.textContent).toBe(
      "show ipv6 route",
    );
    expect(container.querySelectorAll(".cm-aunic-code-block-line").length).toBe(3);
    expect(container.querySelectorAll(".cm-aunic-hidden-markup").length).toBe(0);
  });

  it("keeps active markdown headings sized while showing the raw marker", async () => {
    request.mockResolvedValue(fileSnapshot("note.md", "# Active Heading\nBody", "rev-1"));
    useExplorerStore.setState({ openFile: "note.md" });

    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    const headingLine = container.querySelector(".cm-aunic-heading-1");
    expect(headingLine).not.toBeNull();
    expect(headingLine?.textContent).toContain("# Active Heading");
  });

  it("dispatches write_file on Mod-s", async () => {
    request.mockImplementation(async (type: string) =>
      type === "read_file"
        ? fileSnapshot("note.md", "Body", "rev-1")
        : fileSnapshot("note.md", "Body", "rev-2"),
    );
    useExplorerStore.setState({ openFile: "note.md" });
    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    await act(async () => {
      useNoteEditorStore.getState().markDirty("Changed");
    });
    const content = container.querySelector(".cm-content");
    expect(content).not.toBeNull();

    await act(async () => {
      content?.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "s",
          code: "KeyS",
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        }),
      );
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("write_file", {
      path: "note.md",
      text: "Body",
      expected_revision: "rev-1",
    });
  });

  it("autosaves dirty notes when editor save mode is auto", async () => {
    vi.useFakeTimers();
    request.mockImplementation(async (type: string, payload: { text?: string }) =>
      type === "read_file"
        ? fileSnapshot("note.md", "Body", "rev-1")
        : fileSnapshot("note.md", payload.text ?? "Body", "rev-2"),
    );
    useSessionStore.setState({
      session: sessionState("auto"),
      runActive: false,
    });
    useExplorerStore.setState({ openFile: "note.md" });

    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    await act(async () => {
      useNoteEditorStore.getState().markDirty("Changed");
      await Promise.resolve();
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_250);
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("write_file", {
      path: "note.md",
      text: "Changed",
      expected_revision: "rev-1",
    });
    vi.useRealTimers();
  });

  it("shows reload banners from store state without rendering editor chrome", async () => {
    request.mockResolvedValue(fileSnapshot("note.md", "Body", "rev-1"));
    useExplorerStore.setState({ openFile: "note.md" });
    await act(async () => {
      root.render(<NoteEditor />);
      await flushPromises();
    });

    await act(async () => {
      useNoteEditorStore.getState().markDirty("Changed");
      useNoteEditorStore.setState({
        externalReloadPending: {
          reason: "external-changed",
          snapshot: fileSnapshot("note.md", "Remote", "rev-2"),
        },
      });
    });

    expect(container.textContent).not.toContain("Note Editor");
    expect(container.textContent).toContain("This file changed on disk");
  });
});

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

function sessionState(saveMode: "manual" | "auto") {
  return {
    instance_id: "instance-1",
    run_active: false,
    run_id: null,
    workspace_root: "/workspace",
    default_mode: "note" as const,
    mode: "note" as const,
    work_mode: "off" as const,
    models: [],
    selected_model_index: 0,
    selected_model: {
      label: "Fake",
      provider_name: "codex",
      model: "fake",
      profile_id: null,
      context_window: null,
    },
    pending_permission: null,
    editor_settings: {
      save_mode: saveMode,
    },
  };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
