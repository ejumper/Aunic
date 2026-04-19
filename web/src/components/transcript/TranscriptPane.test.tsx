import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExplorerStore } from "../../state/explorer";
import { useNoteEditorStore } from "../../state/noteEditor";
import { useTranscriptStore } from "../../state/transcript";
import type { FileSnapshotPayload, TranscriptRowPayload } from "../../ws/types";
import { TranscriptPane } from "./TranscriptPane";

const { request, client } = vi.hoisted(() => {
  const request = vi.fn();
  return { request, client: { request } };
});

vi.mock("../../ws/context", () => ({
  useWs: () => ({
    client,
  }),
}));

describe("TranscriptPane", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    request.mockReset();
    useExplorerStore.getState().reset();
    useNoteEditorStore.getState().reset();
    useTranscriptStore.getState().reset();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("renders empty state when the snapshot has no transcript", async () => {
    openSnapshot(fileSnapshot("note.md", "rev-1", []));

    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    expect(container.textContent).toContain("No transcript yet.");
  });

  it("renders chat, bash, search, and fetch row variants", async () => {
    openSnapshot(mixedSnapshot());

    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    expect(container.textContent).toContain("hello from assistant");
    expect(container.textContent).toContain("bash");
    expect(container.textContent).toContain("$ npm test");
    expect(container.textContent).toContain("1 result · Aunic");
    expect(container.textContent).toContain("Fetched page");
  });

  it("filter buttons narrow visible rows", async () => {
    openSnapshot(mixedSnapshot());
    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    await act(async () => {
      getButton("Chat").click();
      await flushPromises();
    });

    expect(container.textContent).toContain("hello from assistant");
    expect(container.textContent).not.toContain("bash");
  });

  it("sort toggle flips row order", async () => {
    openSnapshot(mixedSnapshot());
    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    expect(firstRowNumber()).toBe("#6");

    await act(async () => {
      getButton("Descending").click();
      await flushPromises();
    });

    expect(firstRowNumber()).toBe("#1");
  });

  it("expands a bash row to reveal stdout", async () => {
    openSnapshot(mixedSnapshot());
    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    expect(container.textContent).not.toContain("tests passed");

    await act(async () => {
      const bashRow = rowContaining("bash");
      bashRow.querySelector<HTMLButtonElement>("[aria-expanded='false']")?.click();
      await flushPromises();
    });

    expect(container.textContent).toContain("tests passed");
  });

  it("delete button sends delete_transcript_row", async () => {
    openSnapshot(fileSnapshot("note.md", "rev-1", [messageRow(1, "delete me")]));
    request.mockResolvedValue(fileSnapshot("note.md", "rev-2", []));
    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    await act(async () => {
      container
        .querySelector<HTMLButtonElement>("[aria-label='Delete transcript row 1']")
        ?.click();
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("delete_transcript_row", {
      path: "note.md",
      row_number: 1,
      expected_revision: "rev-1",
    });
  });

  it("renders rows appended through the transcript store", async () => {
    openSnapshot(fileSnapshot("note.md", "rev-1", [messageRow(1, "first")]));
    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    await act(async () => {
      useTranscriptStore.getState().applyLiveRow({
        path: "note.md",
        row: messageRow(2, "live row"),
      });
      await flushPromises();
    });

    expect(container.textContent).toContain("live row");
  });

  it("renders a top-edge resize separator while open", async () => {
    openSnapshot(fileSnapshot("note.md", "rev-1", [messageRow(1, "resize me")]));

    await act(async () => {
      root.render(<TranscriptPane />);
      await flushPromises();
    });

    const separator = container.querySelector<HTMLElement>(
      "[role='separator'][aria-label='Resize transcript']",
    );
    expect(separator).toBeTruthy();
  });

  function getButton(text: string): HTMLButtonElement {
    const button = [...container.querySelectorAll("button")].find(
      (item) => item.textContent === text,
    );
    expect(button).toBeTruthy();
    return button as HTMLButtonElement;
  }

  function firstRowNumber(): string {
    const el = container.querySelector("[data-row-number]");
    return el ? `#${el.getAttribute("data-row-number")}` : "";
  }

  function rowContaining(text: string): Element {
    const row = [...container.querySelectorAll("[data-row-number]")].find((item) =>
      item.textContent?.includes(text),
    );
    expect(row).toBeTruthy();
    return row as Element;
  }
});

function openSnapshot(snapshot: FileSnapshotPayload): void {
  useExplorerStore.setState({ openFile: snapshot.path });
  useNoteEditorStore.setState({
    path: snapshot.path,
    revisionId: snapshot.revision_id,
    snapshot,
    initialDoc: snapshot.note_content,
    currentDoc: snapshot.note_content,
    dirty: false,
  });
}

function mixedSnapshot(): FileSnapshotPayload {
  return fileSnapshot("note.md", "rev-1", [
    messageRow(1, "hello from assistant"),
    toolCallRow(2, "bash", "bash_1", { command: "npm test" }),
    {
      row_number: 3,
      role: "tool",
      type: "tool_result",
      tool_name: "bash",
      tool_id: "bash_1",
      content: { stdout: "tests passed", stderr: "", exit_code: 0 },
    },
    toolCallRow(4, "web_search", "search_1", { queries: ["Aunic"] }),
    {
      row_number: 5,
      role: "tool",
      type: "tool_result",
      tool_name: "web_search",
      tool_id: "search_1",
      content: [{ title: "Aunic", url: "https://example.com", snippet: "result" }],
    },
    {
      row_number: 6,
      role: "tool",
      type: "tool_result",
      tool_name: "web_fetch",
      tool_id: "fetch_1",
      content: { title: "Fetched page", url: "https://example.com/page" },
    },
  ]);
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

function toolCallRow(
  rowNumber: number,
  toolName: string,
  toolId: string,
  content: unknown,
): TranscriptRowPayload {
  return {
    row_number: rowNumber,
    role: "assistant",
    type: "tool_call",
    tool_name: toolName,
    tool_id: toolId,
    content,
  };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
