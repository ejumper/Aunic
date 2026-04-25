import { beforeEach, describe, expect, it, vi } from "vitest";
import { useExplorerStore } from "./explorer";
import { useNoteEditorStore } from "./noteEditor";
import { usePromptStore, type PromptWsClient } from "./prompt";
import { useSessionStore } from "./session";
import type { WsRequestError } from "../ws/client";
import type { ClientRequestType, RequestPayload, RequestResponse } from "../ws/requests";
import type { FileSnapshotPayload } from "../ws/types";

type RequestRecord = {
  type: ClientRequestType;
  payload: unknown;
};

describe("usePromptStore", () => {
  beforeEach(() => {
    useExplorerStore.getState().reset();
    usePromptStore.getState().clear();
    useNoteEditorStore.getState().reset();
    useSessionStore.getState().clearSession();
  });

  it("saves the note before submitting a prompt", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "write_file") {
        return fileSnapshot("note.md", "changed", "rev-2");
      }
      return { run_id: "run-1" };
    });
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "old",
      currentDoc: "changed",
      dirty: true,
    });
    usePromptStore.getState().setDraft("Do the thing");

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(true);

    expect(requests).toEqual([
      {
        type: "write_file",
        payload: {
          path: "note.md",
          text: "changed",
          expected_revision: "rev-1",
        },
      },
      {
        type: "submit_prompt",
        payload: {
          active_file: "note.md",
          included_files: [],
          text: "Do the thing",
          image_attachments: [],
        },
      },
    ]);
    expect(usePromptStore.getState().draft).toBe("");
  });

  it("aborts submit when save-on-send fails", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "write_file") {
        throw new Error("disk failed");
      }
      return { run_id: "run-1" };
    });
    useNoteEditorStore.setState({
      path: "note.md",
      revisionId: "rev-1",
      initialDoc: "old",
      currentDoc: "changed",
      dirty: true,
    });
    usePromptStore.getState().setDraft("Do the thing");

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(false);

    expect(requests.map((item) => item.type)).toEqual(["write_file"]);
    expect(usePromptStore.getState().error).toBeNull();
    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Save failed before the prompt was sent.",
      kind: "error",
    });
  });

  it("surfaces submit_prompt request errors", async () => {
    const client = mockClient(async (type) => {
      if (type === "submit_prompt") {
        throw wsError("run_active");
      }
      return fileSnapshot("note.md", "unchanged", "rev-1");
    });
    usePromptStore.getState().setDraft("Do the thing");

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(false);

    expect(usePromptStore.getState().error).toBeNull();
    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "run_active",
      kind: "error",
    });
    expect(usePromptStore.getState().draft).toBe("Do the thing");
  });

  it("does not submit an empty prompt", async () => {
    const request = vi.fn();

    await expect(
      usePromptStore.getState().submit({ request } as unknown as PromptWsClient, "note.md", []),
    ).resolves.toBe(false);

    expect(request).not.toHaveBeenCalled();
    expect(usePromptStore.getState().error).toBeNull();
    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Enter a prompt before sending.",
      kind: "error",
    });
  });

  it("routes simple mode prompt commands through existing session requests", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "set_mode") {
        return { mode: "chat" };
      }
      return { run_id: "run-1" };
    });
    usePromptStore.getState().setDraft("/chat continue");

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(true);

    expect(requests).toEqual([
      {
        type: "set_mode",
        payload: {
          mode: "chat",
        },
      },
    ]);
    expect(usePromptStore.getState().draft).toBe("continue");
    expect(useSessionStore.getState().indicatorMessage?.text).toBe("Switched to chat mode.");
  });

  it("routes backend prompt commands to run_prompt_command", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "run_prompt_command") {
        return {
          handled: true,
          draft: "continue",
          message: "Switched to chat mode.",
          run_id: null,
          snapshot: null,
        };
      }
      return { run_id: "run-1" };
    });
    useSessionStore.getState().setSession({
      ...sessionPayload(),
      capabilities: { prompt_commands: true },
    });
    usePromptStore.getState().setDraft("/include ./other.md");

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(true);

    expect(requests).toEqual([
      {
        type: "run_prompt_command",
        payload: {
          active_file: "note.md",
          text: "/include ./other.md",
        },
      },
    ]);
    expect(usePromptStore.getState().draft).toBe("continue");
    expect(useSessionStore.getState().indicatorMessage?.text).toBe("Switched to chat mode.");
  });

  it("does not send backend prompt commands to old servers", async () => {
    const request = vi.fn();
    useSessionStore.getState().setSession(sessionPayload());
    usePromptStore.getState().setDraft("@web python");

    await expect(
      usePromptStore.getState().submit({ request } as unknown as PromptWsClient, "note.md", []),
    ).resolves.toBe(false);

    expect(request).not.toHaveBeenCalled();
    expect(useSessionStore.getState().indicatorMessage).toMatchObject({
      text: "Browser server must be restarted before this command can run.",
      kind: "error",
    });
  });

  it("creates and opens a plan through the browser-side /plan command", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      if (type === "set_work_mode") {
        return { work_mode: "plan" };
      }
      if (type === "create_plan") {
        return {
          source_file: "note.md",
          entries: [],
          plans: [
            {
              id: "plan:browser-plan",
              plan_id: "browser-plan",
              path: ".aunic/plans/browser-plan.md",
              name: "browser-plan.md",
              title: "Browser Plan",
              status: "draft",
              active: true,
              exists: true,
              openable: true,
            },
          ],
          active_plan_id: "browser-plan",
        };
      }
      throw new Error(`unexpected request ${type}`);
    });
    useExplorerStore.setState({ openFile: "note.md", sourceFile: "note.md" });
    useSessionStore.getState().setSession({
      ...sessionPayload(),
      capabilities: { prompt_commands: true, plan_flow: true },
    });
    usePromptStore.getState().setDraft("/plan Browser Plan");

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(true);

    expect(requests).toEqual([
      {
        type: "set_work_mode",
        payload: { work_mode: "plan" },
      },
      {
        type: "create_plan",
        payload: { source_file: "note.md", title: "Browser Plan" },
      },
    ]);
    expect(useExplorerStore.getState().sourceFile).toBe("note.md");
    expect(useExplorerStore.getState().openFile).toBe(".aunic/plans/browser-plan.md");
    expect(useSessionStore.getState().indicatorMessage?.text).toBe("Created plan: Browser Plan.");
  });

  it("submits prompt image attachments and clears them after a successful send", async () => {
    const requests: RequestRecord[] = [];
    const client = mockClient(async (type, payload) => {
      requests.push({ type, payload });
      return { run_id: "run-1" };
    });
    useSessionStore.getState().setSession({
      ...sessionPayload(),
      selected_model: {
        ...sessionPayload().selected_model,
        supports_images: true,
      },
    });
    usePromptStore.getState().setDraft("Describe this");
    usePromptStore.getState().addImageAttachments([
      {
        id: "img-1",
        name: "diagram.png",
        data_base64: "ZmFrZQ==",
        size_bytes: 4,
      },
    ]);

    await expect(usePromptStore.getState().submit(client, "note.md", [])).resolves.toBe(true);

    expect(requests).toEqual([
      {
        type: "submit_prompt",
        payload: {
          active_file: "note.md",
          included_files: [],
          text: "Describe this",
          image_attachments: [
            {
              name: "diagram.png",
              data_base64: "ZmFrZQ==",
              size_bytes: 4,
            },
          ],
        },
      },
    ]);
    expect(usePromptStore.getState().imageAttachments).toEqual([]);
  });
});

function mockClient(
  responder: <T extends ClientRequestType>(
    type: T,
    payload: RequestPayload<T>,
  ) => Promise<RequestResponse<T>> | RequestResponse<T>,
): PromptWsClient {
  return {
    request: vi.fn((type, payload) => responder(type, payload)),
  } as unknown as PromptWsClient;
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

function wsError(reason: string): WsRequestError {
  const error = new Error(reason) as WsRequestError;
  error.name = "WsRequestError";
  error.reason = reason;
  return error;
}

function sessionPayload() {
  return {
    instance_id: "instance-1",
    run_active: false,
    run_id: null,
    workspace_root: "/home/ejumps",
    default_mode: "note" as const,
    mode: "note" as const,
    work_mode: "read",
    models: [],
    selected_model_index: 0,
    selected_model: {
      label: "Test",
      provider_name: "test",
      model: "test",
      profile_id: null,
      context_window: null,
      supports_images: false,
      image_transport: "unsupported" as const,
    },
    pending_permission: null,
  };
}
