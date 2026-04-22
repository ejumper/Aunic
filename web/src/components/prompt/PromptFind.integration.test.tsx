import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  replaceAllBrowserMatches,
  setBrowserFindText,
  setBrowserReplaceText,
} from "../../browserFind";
import { noteEditorRef } from "../../noteEditorRef";
import { useExplorerStore } from "../../state/explorer";
import { useFindStore } from "../../state/find";
import { useNoteEditorStore } from "../../state/noteEditor";
import { usePromptStore } from "../../state/prompt";
import { useSessionStore } from "../../state/session";
import type { FileSnapshotPayload, SessionStatePayload } from "../../ws/types";
import { NoteEditor } from "../editor/NoteEditor";
import { PromptComposer } from "./PromptComposer";

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

describe("PromptFind integration", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
    request.mockReset();
    request.mockResolvedValue(fileSnapshot("note.md", "alpha beta gamma beta", "rev-1"));
    useExplorerStore.getState().reset();
    useFindStore.getState().reset();
    useNoteEditorStore.getState().reset();
    usePromptStore.getState().clear();
    useSessionStore.getState().clearSession();
    Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
    Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
    window.requestAnimationFrame = (callback: FrameRequestCallback) =>
      window.setTimeout(() => callback(performance.now()), 0);
    window.cancelAnimationFrame = (id: number) => window.clearTimeout(id);
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    openWorkspace();
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("turns the prompt dock into find and replace instead of opening a CodeMirror panel", async () => {
    await act(async () => {
      root.render(
        <>
          <NoteEditor />
          <PromptComposer />
        </>,
      );
      await flushPromises();
    });

    await act(async () => {
      noteEditorRef.get()?.focus();
      document.dispatchEvent(
        new KeyboardEvent("keydown", {
          key: "f",
          code: "KeyF",
          ctrlKey: true,
          bubbles: true,
          cancelable: true,
        }),
      );
      await flushPromises();
    });

    expect(container.querySelector("[aria-label='Prompt editor']")).toBeNull();
    expect(container.querySelector(".prompt-find")).not.toBeNull();
    expect(container.querySelector(".cm-panels")).toBeNull();

    const inputs = container.querySelectorAll<HTMLInputElement>(".prompt-find__input");
    expect(inputs).toHaveLength(1);

    await act(async () => {
      setBrowserFindText("beta");
      await flushPromises();
    });

    await act(async () => {
      button(container, "Replace")?.click();
      await flushPromises();
    });

    const replaceInput = container.querySelectorAll<HTMLInputElement>(".prompt-find__input")[1];
    expect(replaceInput).toBeTruthy();

    await act(async () => {
      setBrowserReplaceText("BETA");
      replaceAllBrowserMatches();
      await flushPromises();
    });

    expect(useNoteEditorStore.getState().currentDoc).toBe("alpha BETA gamma BETA");
  });
});

function button(container: HTMLElement, text: string): HTMLButtonElement | null {
  return (
    [...container.querySelectorAll("button")].find((item) => item.textContent === text) ?? null
  );
}

function openWorkspace(overrides: Partial<SessionStatePayload> = {}): void {
  useExplorerStore.setState({ openFile: "note.md" });
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

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
