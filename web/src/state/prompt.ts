import { create } from "zustand";
import { useNoteEditorStore } from "./noteEditor";
import { useSessionStore } from "./session";
import { useTranscriptStore } from "./transcript";
import { parsePromptCommand, type PromptCommand } from "../promptCommands";
import type { WsClient, WsRequestError } from "../ws/client";

export type PromptWsClient = Pick<WsClient, "request">;

export interface PromptSlice {
  draft: string;
  documentVersion: number;
  submitting: boolean;
  error: string | null;
  lastSubmittedAt: Date | null;
  setDraft: (text: string) => void;
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
};

export const usePromptStore = create<PromptSlice>((set, get) => ({
  ...EMPTY_STATE,

  setDraft(text) {
    set({ draft: text, error: null });
  },

  async submit(client, activeFile, includedFiles = []) {
    const text = get().draft;
    if (!activeFile) {
      setPromptIndicator("Open a file before sending a prompt.", "error");
      set({ error: null });
      return false;
    }
    if (!text.trim()) {
      setPromptIndicator("Enter a prompt before sending.", "error");
      set({ error: null });
      return false;
    }
    if (useSessionStore.getState().runActive) {
      setPromptIndicator("Wait for the current run to finish before sending another prompt.", "error");
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
        const simpleCommandResponse = await runSimplePromptCommand(
          client,
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
        return response.handled;
      }

      await client.request("submit_prompt", {
        active_file: activeFile,
        included_files: includedFiles,
        text,
      });
      set({
        draft: "",
        documentVersion: get().documentVersion + 1,
        submitting: false,
        error: null,
        lastSubmittedAt: new Date(),
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
  return null;
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
