import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useExplorerStore } from "../../state/explorer";
import { useNoteEditorStore } from "../../state/noteEditor";
import { usePromptStore } from "../../state/prompt";
import { useSessionStore } from "../../state/session";
import type { FileSnapshotPayload, SessionStatePayload } from "../../ws/types";
import { PromptComposer } from "./PromptComposer";

const { request, client } = vi.hoisted(() => {
  const request = vi.fn();
  return { request, client: { request } };
});

vi.mock("../../ws/context", () => ({
  useWs: () => ({
    client,
  }),
}));

describe("PromptComposer", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    request.mockReset();
    request.mockResolvedValue({ run_id: "run-1" });
    useExplorerStore.getState().reset();
    useNoteEditorStore.getState().reset();
    usePromptStore.getState().clear();
    useSessionStore.getState().clearSession();
    Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
    Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
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

  it("renders null when no file is open", async () => {
    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    expect(container.textContent).toBe("");
  });

  it("disables controls and shows cancel while a run is active", async () => {
    openComposer({ run_active: true, run_id: "run-1" });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    expect(button("Cancel")).not.toBeNull();
    expect(button("Mode: Note")?.disabled).toBe(true);
    expect(button("Agent: Off")?.disabled).toBe(true);
    expect(container.querySelector<HTMLSelectElement>("select")?.disabled).toBe(true);
  });

  it("renders the pending permission banner", async () => {
    openComposer({
      pending_permission: {
        permission_id: "perm-1",
        request: {
          tool_name: "bash",
          action: "run",
          target: "pwd",
          message: "Run command?",
          policy: "ask",
          key: null,
          details: null,
        },
      },
    });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    expect(container.textContent).toContain("Run command?");
    expect(button("Once")).not.toBeNull();
    expect(button("Always")).not.toBeNull();
    expect(button("Reject")).not.toBeNull();
  });

  it("submits on Shift+Enter", async () => {
    openComposer();
    usePromptStore.getState().setDraft("Do the thing");

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    await act(async () => {
      container.querySelector(".cm-content")?.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "Enter",
          code: "Enter",
          shiftKey: true,
          bubbles: true,
          cancelable: true,
        }),
      );
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("submit_prompt", {
      active_file: "note.md",
      included_files: [],
      text: "Do the thing",
    });
  });

  it("cycles mode and agent controls", async () => {
    openComposer();

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    await act(async () => {
      button("Mode: Note")?.click();
      button("Agent: Off")?.click();
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("set_mode", { mode: "chat" });
    expect(request).toHaveBeenCalledWith("set_work_mode", { work_mode: "read" });
  });

  it("fills the context meter from session usage and unsaved note edits", async () => {
    openComposer({
      context_usage: {
        tokens_used: 1_000,
        context_window: 2_000,
        fraction: 0.5,
        last_note_chars: 4,
      },
    });
    useNoteEditorStore.setState({ currentDoc: "base plus eight" });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    const meter = container.querySelector<HTMLElement>(".context-meter");
    expect(meter).not.toBeNull();
    expect(meter?.style.getPropertyValue("--context-fill")).toBe("50.10%");
    expect(meter?.getAttribute("aria-label")).toContain("Context usage ~1,002 / 2,000 tokens");
  });

  it("replaces the prompt editor with research results", async () => {
    request.mockResolvedValue(fileSnapshot("note.md", "rev-2"));
    openComposer({
      research_state: {
        mode: "results",
        source: "web",
        query: "python",
        scope: null,
        busy: null,
        results: [
          {
            title: "Python",
            url: "https://www.python.org/",
            snippet: "Official Python site",
            source: null,
            result_id: null,
            local_path: null,
            score: 1,
            heading_path: [],
          },
        ],
        packet: null,
      },
    });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    expect(container.textContent).toContain("1 result for \"python\"");
    expect(container.querySelector("[aria-label='Prompt editor']")).toBeNull();

    await act(async () => {
      container.querySelector<HTMLInputElement>("input[type='checkbox']")?.click();
      await flushPromises();
    });
    await act(async () => {
      button("Fetch")?.click();
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("research_fetch_result", {
      active_file: "note.md",
      result_index: 0,
    });
  });

  it("navigates and expands research results with arrow keys", async () => {
    request.mockResolvedValue(fileSnapshot("note.md", "rev-2"));
    openComposer({
      research_state: {
        mode: "results",
        source: "web",
        query: "python",
        scope: null,
        busy: null,
        results: [
          {
            title: "Python",
            url: "https://www.python.org/",
            snippet: `${"Official Python site. ".repeat(20)}first hidden detail`,
            source: null,
            result_id: null,
            local_path: null,
            score: 1,
            heading_path: [],
          },
          {
            title: "Python Docs",
            url: "https://docs.python.org/",
            snippet: "Python documentation",
            source: null,
            result_id: null,
            local_path: null,
            score: 0.9,
            heading_path: [],
          },
        ],
        packet: null,
      },
    });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    const picker = researchPicker();
    expect(container.textContent).not.toContain("first hidden detail");

    await act(async () => {
      picker.dispatchEvent(key("ArrowRight"));
      await flushPromises();
    });
    expect(container.textContent).toContain("first hidden detail");

    await act(async () => {
      picker.dispatchEvent(key("ArrowLeft"));
      await flushPromises();
    });
    expect(container.textContent).not.toContain("first hidden detail");

    await act(async () => {
      picker.dispatchEvent(key("ArrowDown"));
      await flushPromises();
    });
    await act(async () => {
      picker.dispatchEvent(key(" "));
      await flushPromises();
    });
    await act(async () => {
      button("Fetch")?.click();
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("research_fetch_result", {
      active_file: "note.md",
      result_index: 1,
    });
  });

  it("inserts selected research chunks", async () => {
    request.mockResolvedValue(fileSnapshot("note.md", "rev-2"));
    openComposer({
      research_state: {
        mode: "chunks",
        source: "rag",
        query: "stp",
        scope: null,
        busy: null,
        results: [],
        packet: {
          title: "STP",
          url: null,
          full_text_available: true,
          source: "docs",
          result_id: "docs:chunk:1",
          total_chunks: 1,
          truncated: false,
          chunks: [
            {
              title: "Root Bridge",
              url: "docs/stp.md",
              text: "Root bridge election details",
              score: 1,
              heading_path: ["Networking", "STP"],
              chunk_id: "chunk-1",
              chunk_order: 0,
              is_match: true,
            },
          ],
        },
      },
    });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    await act(async () => {
      container.querySelector<HTMLInputElement>("input[type='checkbox']")?.click();
      await flushPromises();
    });
    await act(async () => {
      button("Insert selected")?.click();
      await flushPromises();
    });

    expect(request).toHaveBeenCalledWith("research_insert_chunks", {
      active_file: "note.md",
      mode: "selected_chunks",
      chunk_indices: [0],
    });
  });

  it("expands and collapses focused research chunks with arrow keys", async () => {
    openComposer({
      research_state: {
        mode: "chunks",
        source: "rag",
        query: "stp",
        scope: null,
        busy: null,
        results: [],
        packet: {
          title: "STP",
          url: null,
          full_text_available: true,
          source: "docs",
          result_id: "docs:chunk:1",
          total_chunks: 1,
          truncated: false,
          chunks: [
            {
              title: "Root Bridge",
              url: "docs/stp.md",
              text: `${"Root bridge election details. ".repeat(18)}chunk hidden detail`,
              score: 1,
              heading_path: ["Networking", "STP"],
              chunk_id: "chunk-1",
              chunk_order: 0,
              is_match: true,
            },
          ],
        },
      },
    });

    await act(async () => {
      root.render(<PromptComposer />);
      await flushPromises();
    });

    const picker = researchPicker();
    expect(container.textContent).not.toContain("chunk hidden detail");

    await act(async () => {
      picker.dispatchEvent(key("ArrowDown"));
      await flushPromises();
    });
    await act(async () => {
      picker.dispatchEvent(key("ArrowRight"));
      await flushPromises();
    });
    expect(container.textContent).toContain("chunk hidden detail");

    await act(async () => {
      picker.dispatchEvent(key("ArrowLeft"));
      await flushPromises();
    });
    expect(container.textContent).not.toContain("chunk hidden detail");
  });

  function button(text: string): HTMLButtonElement | null {
    return (
      [...container.querySelectorAll("button")].find(
        (item) => item.textContent === text,
      ) ?? null
    );
  }

  function researchPicker(): HTMLElement {
    const picker = container.querySelector<HTMLElement>(".research-picker");
    expect(picker).toBeTruthy();
    return picker as HTMLElement;
  }
});

function openComposer(overrides: Partial<SessionStatePayload> = {}): void {
  useExplorerStore.setState({ openFile: "note.md" });
  useNoteEditorStore.setState({
    path: "note.md",
    revisionId: "rev-1",
    initialDoc: "base",
    currentDoc: "base",
    dirty: false,
  });
  useSessionStore.getState().setSession({ ...sessionPayload(), ...overrides });
}

function sessionPayload(): SessionStatePayload {
  return {
    run_active: false,
    run_id: null,
    workspace_root: "/home/ejumps",
    default_mode: "note",
    mode: "note",
    work_mode: "off",
    models: [
      {
        label: "Codex",
        provider_name: "codex",
        model: "gpt-5.4",
        profile_id: null,
        context_window: null,
      },
      {
        label: "Claude",
        provider_name: "claude",
        model: "claude-sonnet",
        profile_id: null,
        context_window: null,
      },
    ],
    selected_model_index: 0,
    selected_model: {
      label: "Codex",
      provider_name: "codex",
      model: "gpt-5.4",
      profile_id: null,
      context_window: null,
    },
    pending_permission: null,
    capabilities: { prompt_commands: true, research_flow: true },
  };
}

function fileSnapshot(path: string, revisionId: string): FileSnapshotPayload {
  return {
    path,
    revision_id: revisionId,
    content_hash: revisionId,
    mtime_ns: 1,
    size_bytes: 4,
    captured_at: "2026-04-17T00:00:00Z",
    note_content: "base",
    transcript_rows: [],
    has_transcript: false,
  };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

function key(keyValue: string): KeyboardEvent {
  return new KeyboardEvent("keydown", {
    key: keyValue,
    bubbles: true,
    cancelable: true,
  });
}
