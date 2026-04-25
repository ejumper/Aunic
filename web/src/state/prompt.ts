import { create } from "zustand";
import { useExplorerStore } from "./explorer";
import { useNoteEditorStore } from "./noteEditor";
import { useSessionStore } from "./session";
import { useTranscriptStore } from "./transcript";
import { parsePromptCommand, type PromptCommand } from "../promptCommands";
import type { WsClient, WsRequestError } from "../ws/client";
import type { PromptImageAttachmentPayload } from "../ws/types";

export type PromptWsClient = Pick<WsClient, "request">;

export interface PromptImageAttachment extends PromptImageAttachmentPayload {
  id: string;
}

export interface PromptSlice {
  draft: string;
  documentVersion: number;
  submitting: boolean;
  error: string | null;
  lastSubmittedAt: Date | null;
  imageAttachments: PromptImageAttachment[];
  setDraft: (text: string) => void;
  addImageAttachments: (attachments: PromptImageAttachment[]) => void;
  removeImageAttachment: (id: string) => void;
  clearImageAttachments: () => void;
  submit: (
    client: PromptWsClient,
    activeFile: string | null,
    includedFiles?: string[],
  ) => Promise<boolean>;
  cancel: (client: PromptWsClient, runId: string | null) => Promise<boolean>;
  clear: () => void;
}

const EMPTY_STATE = {
  draft: "",
  documentVersion: 0,
  submitting: false,
  error: null,
  lastSubmittedAt: null,
  imageAttachments: [],
};

export const usePromptStore = create<PromptSlice>((set, get) => ({
  ...EMPTY_STATE,

  setDraft(text) {
    set({ draft: text, error: null });
  },

  addImageAttachments(attachments) {
    set((state) => {
      const existing = new Map(state.imageAttachments.map((attachment) => [attachment.id, attachment]));
      for (const attachment of attachments) {
        existing.set(attachment.id, attachment);
      }
      return {
        imageAttachments: [...existing.values()],
        error: null,
      };
    });
  },

  removeImageAttachment(id) {
    set((state) => ({
      imageAttachments: state.imageAttachments.filter((attachment) => attachment.id !== id),
      error: null,
    }));
  },

  clearImageAttachments() {
    set({ imageAttachments: [], error: null });
  },

  async submit(client, activeFile, includedFiles = []) {
    const text = get().draft;
    const imageAttachments = get().imageAttachments;
    if (!activeFile) {
      setPromptIndicator("Open a file before sending a prompt.", "error");
      set({ error: null });
      return false;
    }
    if (!text.trim() && imageAttachments.length === 0) {
      setPromptIndicator("Enter a prompt before sending.", "error");
      set({ error: null });
      return false;
    }
    if (useSessionStore.getState().runActive) {
      setPromptIndicator("Wait for the current run to finish before sending another prompt.", "error");
      set({ error: null });
      return false;
    }
    if (
      imageAttachments.length > 0 &&
      !useSessionStore.getState().session?.selected_model.supports_images
    ) {
      setPromptIndicator(
        `${useSessionStore.getState().session?.selected_model.label ?? "This model"} does not support image inputs.`,
        "error",
      );
      set({ error: null });
      return false;
    }

    set({ submitting: true, error: null });
    try {
      const noteState = useNoteEditorStore.getState();
      const saved = await noteState.saveIfDirty(client, noteState.currentDoc);
      if (!saved) {
        setPromptIndicator("Save failed before the prompt was sent.", "error");
        set({
          submitting: false,
          error: null,
        });
        return false;
      }

      const commandMatch = parsePromptCommand(text);
      if (commandMatch) {
        if (imageAttachments.length > 0) {
          setPromptIndicator("Prompt commands do not support image attachments.", "error");
          set({
            submitting: false,
            error: null,
          });
          return false;
        }
        if (
          commandMatch.command === "/plan" &&
          !useSessionStore.getState().session?.capabilities?.plan_flow
        ) {
          setPromptIndicator(
            "Browser server must be restarted before plan commands can run.",
            "error",
          );
          set({
            submitting: false,
            error: null,
          });
          return false;
        }

        const simpleCommandResponse = await runSimplePromptCommand(
          client,
          activeFile,
          commandMatch.command,
          commandMatch.remaining,
        );
        if (simpleCommandResponse) {
          set({
            draft: simpleCommandResponse.draft,
            documentVersion: get().documentVersion + 1,
            submitting: false,
            error: null,
            lastSubmittedAt: new Date(),
          });
          setPromptIndicator(simpleCommandResponse.message);
          return true;
        }

        if (!useSessionStore.getState().session?.capabilities?.prompt_commands) {
          setPromptIndicator(
            "Browser server must be restarted before this command can run.",
            "error",
          );
          set({
            submitting: false,
            error: null,
          });
          return false;
        }
        if (
          (commandMatch.command === "@web" || commandMatch.command === "@rag") &&
          !useSessionStore.getState().session?.capabilities?.research_flow
        ) {
          setPromptIndicator(
            "Browser server must be restarted before research commands can run.",
            "error",
          );
          set({
            submitting: false,
            error: null,
          });
          return false;
        }

        const response = await client.request("run_prompt_command", {
          active_file: activeFile,
          text,
        });
        if (response.snapshot) {
          useNoteEditorStore.getState().applySnapshot(response.snapshot, {
            remountEditor: false,
            notice: response.message,
          });
          useTranscriptStore.getState().loadFromSnapshot(response.snapshot);
        }
        set({
          draft: response.draft,
          documentVersion: get().documentVersion + 1,
          submitting: false,
          error: null,
          lastSubmittedAt: new Date(),
        });
        if (response.message) {
          setPromptIndicator(response.message);
        }
        if (
          (commandMatch.command === "/include" || commandMatch.command === "/exclude") &&
          useExplorerStore.getState().sourceFile === activeFile
        ) {
          void useExplorerStore.getState().refreshProjectState(client);
        }
        return response.handled;
      }

      await client.request("submit_prompt", {
        active_file: activeFile,
        included_files: includedFiles,
        text,
        image_attachments: imageAttachments.map(({ id: _id, ...attachment }) => attachment),
      });
      set({
        draft: "",
        documentVersion: get().documentVersion + 1,
        submitting: false,
        error: null,
        lastSubmittedAt: new Date(),
        imageAttachments: [],
      });
      return true;
    } catch (error) {
      setPromptIndicator(formatPromptError(error), "error");
      set({
        submitting: false,
        error: null,
      });
      return false;
    }
  },

  async cancel(client, runId) {
    set({ submitting: true, error: null });
    try {
      const response = await client.request("cancel_run", { run_id: runId });
      set({ submitting: false });
      return response.cancelled;
    } catch (error) {
      setPromptIndicator(formatPromptError(error), "error");
      set({
        submitting: false,
        error: null,
      });
      return false;
    }
  },

  clear() {
    set((state) => ({ ...EMPTY_STATE, documentVersion: state.documentVersion + 1 }));
  },
}));

async function runSimplePromptCommand(
  client: PromptWsClient,
  activeFile: string,
  command: PromptCommand,
  remaining: string,
): Promise<{ draft: string; message: string } | null> {
  if (command === "/note" || command === "/chat") {
    const mode = command.slice(1) as "note" | "chat";
    await client.request("set_mode", { mode });
    return {
      draft: remaining,
      message: `Switched to ${mode} mode.`,
    };
  }
  if (command === "/work" || command === "/read" || command === "/off") {
    const workMode = command.slice(1) as "work" | "read" | "off";
    await client.request("set_work_mode", { work_mode: workMode });
    return {
      draft: remaining,
      message: `Agent mode set to ${workMode}.`,
    };
  }
  if (command === "/plan") {
    return runPlanPromptCommand(client, activeFile, remaining);
  }
  return null;
}

async function runPlanPromptCommand(
  client: PromptWsClient,
  activeFile: string,
  remaining: string,
): Promise<{ draft: string; message: string }> {
  const explorer = useExplorerStore.getState();
  const sourceFile = explorer.sourceFile ?? activeFile;
  const trimmed = remaining.trim();
  await client.request("set_work_mode", { work_mode: "plan" });
  useExplorerStore.getState().setViewMode("project");

  if (trimmed === "list") {
    await useExplorerStore.getState().loadProjectState(client, sourceFile);
    return {
      draft: "",
      message: "Plans are listed in project files.",
    };
  }

  if (trimmed && trimmed !== "open") {
    const projectState = await useExplorerStore.getState().createPlan(client, trimmed);
    const plan = projectState.plans.find((item) => item.plan_id === projectState.active_plan_id);
    if (plan) {
      useExplorerStore.getState().openProjectFile(plan.path, plan.id);
      useExplorerStore.getState().selectProject(plan.id);
      return {
        draft: "",
        message: `Created plan: ${plan.title}.`,
      };
    }
    return {
      draft: "",
      message: "Created plan.",
    };
  }

  await useExplorerStore.getState().loadProjectState(client, sourceFile);
  let projectState = useExplorerStore.getState().projectState;
  if (!projectState) {
    throw new Error("Project state is unavailable.");
  }

  const openPlan = (planId: string | null): { draft: string; message: string } | null => {
    if (!planId) {
      return null;
    }
    const plan = projectState?.plans.find((item) => item.plan_id === planId);
    if (!plan) {
      return null;
    }
    useExplorerStore.getState().openProjectFile(plan.path, plan.id);
    useExplorerStore.getState().selectProject(plan.id);
    return {
      draft: "",
      message: `Opened plan: ${plan.title}.`,
    };
  };

  if (trimmed === "open") {
    const opened = openPlan(projectState.active_plan_id);
    if (opened) {
      return opened;
    }
    throw new Error("No active plan to open.");
  }

  const openedActive = openPlan(projectState.active_plan_id);
  if (openedActive) {
    return openedActive;
  }

  const drafts = projectState.plans.filter(
    (plan) => plan.status === "draft" || plan.status === "awaiting_approval",
  );
  if (drafts.length === 1) {
    projectState = await useExplorerStore.getState().setActivePlan(client, drafts[0].plan_id);
    const openedDraft = openPlan(projectState.active_plan_id);
    if (openedDraft) {
      return openedDraft;
    }
  }
  if (drafts.length > 1) {
    return {
      draft: "",
      message: "Multiple draft plans found. Choose one from project files.",
    };
  }

  const createdState = await useExplorerStore.getState().createPlan(client, "Untitled Plan");
  const created = createdState.plans.find((plan) => plan.plan_id === createdState.active_plan_id);
  if (created) {
    useExplorerStore.getState().openProjectFile(created.path, created.id);
    useExplorerStore.getState().selectProject(created.id);
    return {
      draft: "",
      message: `Created plan: ${created.title}.`,
    };
  }
  return {
    draft: "",
    message: "Created plan.",
  };
}

function setPromptIndicator(text: string, kind = "status"): void {
  useSessionStore.getState().setIndicatorMessage(text, kind);
}

function isWsRequestError(error: unknown): error is WsRequestError {
  return (
    error instanceof Error &&
    "reason" in error &&
    typeof (error as WsRequestError).reason === "string"
  );
}

function formatPromptError(error: unknown): string {
  if (isWsRequestError(error)) {
    return `${error.reason}${error.details ? ` ${JSON.stringify(error.details)}` : ""}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
