import {
  useCallback,
  useEffect,
  useRef,
  type ChangeEvent,
  type ClipboardEvent,
  type CSSProperties,
  type DragEvent,
} from "react";
import {
  closeBrowserFind,
  findNextBrowserMatch,
  findPreviousBrowserMatch,
  openBrowserFind,
  setBrowserFindReplaceMode,
} from "../../browserFind";
import { useExplorerStore } from "../../state/explorer";
import { useFindStore } from "../../state/find";
import { useNoteEditorStore } from "../../state/noteEditor";
import { usePromptStore } from "../../state/prompt";
import { useSessionStore } from "../../state/session";
import { type WsRequestError } from "../../ws/client";
import { useWs } from "../../ws/context";
import type {
  BrowserMode,
  PermissionResolution,
  SessionStatePayload,
  WorkMode,
} from "../../ws/types";
import { ResearchPicker } from "../research/ResearchPicker";
import { IndicatorLine } from "./IndicatorLine";
import { ModeSwitcher } from "./ModeSwitcher";
import { ModelPicker } from "./ModelPicker";
import { PermissionPrompt } from "./PermissionPrompt";
import { PromptFind } from "./PromptFind";
import { PromptEditor } from "./PromptEditor";
import { SendCancelControls } from "./SendCancelControls";
import { WorkModeSwitcher } from "./WorkModeSwitcher";
import { CmdsMenu } from "./CmdsMenu";

export function PromptComposer() {
  const { client } = useWs();
  const openFile = useExplorerStore((store) => store.openFile);
  const sourceFile = useExplorerStore((store) => store.sourceFile);
  const noteStatus = useNoteEditorStore((store) => store.status);
  const session = useSessionStore((store) => store.session);
  const runActive = useSessionStore((store) => store.runActive);
  const currentRunId = useSessionStore((store) => store.currentRunId);
  const pendingPermission = useSessionStore((store) => store.pendingPermission);
  const indicatorMessage = useSessionStore((store) => store.indicatorMessage);
  const clearPendingPermission = useSessionStore((store) => store.clearPendingPermission);
  const setIndicatorMessage = useSessionStore((store) => store.setIndicatorMessage);
  const draft = usePromptStore((store) => store.draft);
  const documentVersion = usePromptStore((store) => store.documentVersion);
  const submitting = usePromptStore((store) => store.submitting);
  const imageAttachments = usePromptStore((store) => store.imageAttachments);
  const findActive = useFindStore((store) => store.active);
  const setDraft = usePromptStore((store) => store.setDraft);
  const addImageAttachments = usePromptStore((store) => store.addImageAttachments);
  const removeImageAttachment = usePromptStore((store) => store.removeImageAttachment);
  const submit = usePromptStore((store) => store.submit);
  const cancel = usePromptStore((store) => store.cancel);
  const currentDoc = useNoteEditorStore((store) => store.currentDoc);
  const promptTargetFile = sourceFile ?? openFile;
  const attachmentInputRef = useRef<HTMLInputElement | null>(null);

  const sendPrompt = useCallback(() => {
    void submit(client, promptTargetFile, []);
  }, [client, promptTargetFile, submit]);

  const cancelRun = useCallback(() => {
    void cancel(client, currentRunId);
  }, [cancel, client, currentRunId]);

  const setMode = useCallback(
    async (mode: BrowserMode) => {
      try {
        await client.request("set_mode", { mode });
        setIndicatorMessage(`Switched to ${mode} mode.`);
      } catch (error) {
        setIndicatorMessage(formatControlError(error), "error");
      }
    },
    [client, setIndicatorMessage],
  );

  const setWorkMode = useCallback(
    async (workMode: WorkMode) => {
      try {
        await client.request("set_work_mode", { work_mode: workMode });
        setIndicatorMessage(`Agent mode set to ${workMode}.`);
      } catch (error) {
        setIndicatorMessage(formatControlError(error), "error");
      }
    },
    [client, setIndicatorMessage],
  );

  const selectModel = useCallback(
    async (index: number) => {
      try {
        await client.request("select_model", { index });
        const label = session?.models[index]?.label ?? "model";
        setIndicatorMessage(`Selected model: ${label}.`);
      } catch (error) {
        setIndicatorMessage(formatControlError(error), "error");
      }
    },
    [client, session, setIndicatorMessage],
  );

  const resolvePermission = useCallback(
    async (resolution: PermissionResolution) => {
      if (!pendingPermission) {
        return;
      }
      try {
        await client.request("resolve_permission", {
          permission_id: pendingPermission.permission_id,
          resolution,
        });
        clearPendingPermission();
      } catch (error) {
        setIndicatorMessage(formatControlError(error), "error");
      }
    },
    [clearPendingPermission, client, pendingPermission, setIndicatorMessage],
  );

  const controlsDisabled = runActive || !session;
  const researchState = session?.research_state;
  const researchActive =
    Boolean(researchState) && researchState?.mode !== undefined && researchState.mode !== "idle";
  const canSubmit =
    !runActive &&
    !submitting &&
    noteStatus !== "loading" &&
    noteStatus !== "saving" &&
    (draft.trim().length > 0 || imageAttachments.length > 0);
  const contextMeter = contextMeterState(session, currentDoc);
  const selectedModelSupportsImages = Boolean(session?.selected_model.supports_images);

  const handleImageFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) {
        return;
      }
      if (!selectedModelSupportsImages) {
        setIndicatorMessage(
          `${session?.selected_model.label ?? "This model"} does not support image inputs.`,
          "error",
        );
        return;
      }
      const accepted = files.filter(isSupportedImageFile);
      if (accepted.length === 0) {
        setIndicatorMessage("Only PNG, JPEG, WEBP, and GIF images can be attached.", "error");
        return;
      }
      try {
        const attachments = await Promise.all(accepted.map(fileToPromptAttachment));
        addImageAttachments(attachments);
        if (accepted.length !== files.length) {
          setIndicatorMessage(
            `Attached ${attachments.length} image${attachments.length === 1 ? "" : "s"} and ignored unsupported files.`,
          );
        } else {
          setIndicatorMessage(
            `Attached ${attachments.length} image${attachments.length === 1 ? "" : "s"}.`,
          );
        }
      } catch (error) {
        setIndicatorMessage(formatControlError(error), "error");
      }
    },
    [addImageAttachments, selectedModelSupportsImages, session, setIndicatorMessage],
  );

  const handleAttachmentInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = event.target.files ? [...event.target.files] : [];
      void handleImageFiles(files);
      event.target.value = "";
    },
    [handleImageFiles],
  );

  const handlePromptDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      if (!event.dataTransfer.files.length) {
        return;
      }
      event.preventDefault();
      void handleImageFiles([...event.dataTransfer.files]);
    },
    [handleImageFiles],
  );

  const handlePromptPaste = useCallback(
    (event: ClipboardEvent<HTMLDivElement>) => {
      if (!(event.target instanceof Element) || !event.target.closest(".prompt-editor-host")) {
        return;
      }
      const files = imageFilesFromClipboard(event.clipboardData);
      if (files.length === 0) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      void handleImageFiles(files);
    },
    [handleImageFiles],
  );

  const handlePromptDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    if (!event.dataTransfer.types.includes("Files")) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  useEffect(() => {
    if (!openFile) {
      return;
    }

    function focusFindInput() {
      requestAnimationFrame(() => {
        const input = document.querySelector<HTMLInputElement>(".prompt-find__input");
        input?.focus();
        input?.select();
      });
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (pendingPermission || researchActive) {
        return;
      }
      const activeElement = document.activeElement;
      const eligibleTarget =
        activeElement === document.body ||
        (activeElement instanceof HTMLElement &&
          Boolean(
            activeElement.closest(".code-editor-host") ||
              activeElement.closest(".prompt-editor-host") ||
              activeElement.closest(".prompt-find"),
          ));
      if (!eligibleTarget) {
        return;
      }

      const modKey = event.ctrlKey || event.metaKey;
      const lowerKey = event.key.toLowerCase();
      const inFindUi =
        activeElement instanceof HTMLElement && Boolean(activeElement.closest(".prompt-find"));
      const inNoteEditor =
        activeElement instanceof HTMLElement && Boolean(activeElement.closest(".code-editor-host"));

      if (modKey && lowerKey === "f" && !event.altKey) {
        event.preventDefault();
        event.stopPropagation();
        if (useFindStore.getState().active && inFindUi) {
          setBrowserFindReplaceMode(!useFindStore.getState().replaceMode);
        } else {
          openBrowserFind();
        }
        focusFindInput();
        return;
      }

      if (modKey && lowerKey === "h" && !event.altKey) {
        event.preventDefault();
        event.stopPropagation();
        openBrowserFind({ replaceMode: true });
        focusFindInput();
        return;
      }

      if (
        event.key === "F3" ||
        (modKey && lowerKey === "g" && !event.altKey)
      ) {
        event.preventDefault();
        event.stopPropagation();
        if (event.shiftKey) {
          findPreviousBrowserMatch();
        } else {
          findNextBrowserMatch();
        }
        return;
      }

      if (event.key === "Escape" && useFindStore.getState().active) {
        if (!inFindUi && !inNoteEditor) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        closeBrowserFind({ restoreFocus: inNoteEditor ? "note" : "prompt" });
      }
    }

    document.addEventListener("keydown", handleKeyDown, true);
    return () => document.removeEventListener("keydown", handleKeyDown, true);
  }, [openFile, pendingPermission, researchActive]);

  if (!openFile || !promptTargetFile) {
    return null;
  }

  return (
    <section className="prompt-composer" aria-label="Prompt composer">
      {pendingPermission ? (
        <PermissionPrompt permission={pendingPermission} onResolve={resolvePermission} />
      ) : null}

      <IndicatorLine
        session={session}
        indicator={indicatorMessage}
        attachments={imageAttachments}
        onRemoveAttachment={removeImageAttachment}
      />

      <input
        ref={attachmentInputRef}
        type="file"
        accept="image/*"
        multiple
        tabIndex={-1}
        className="prompt-attachment-input"
        onChange={handleAttachmentInputChange}
      />

      <div
        className="prompt-message-block"
        onDragOver={handlePromptDragOver}
        onDrop={handlePromptDrop}
        onPasteCapture={handlePromptPaste}
      >
        {researchActive && !pendingPermission && researchState ? (
          <ResearchPicker client={client} sourceFile={promptTargetFile} research={researchState} />
        ) : findActive ? (
          <PromptFind />
        ) : (
          <>
            <PromptEditor
              value={draft}
              documentVersion={documentVersion}
              runActive={runActive}
              onChange={setDraft}
              onSubmit={sendPrompt}
              onCancel={cancelRun}
            />

            <div
              className="context-meter"
              aria-label={contextMeter.ariaLabel}
              role="img"
              title={contextMeter.title}
              style={contextMeter.style}
            />

            <div className="prompt-composer__footer">
              <div className="prompt-composer__controls">
                <button
                  type="button"
                  className="mode-pill prompt-attach-button"
                  disabled={controlsDisabled || !selectedModelSupportsImages}
                  aria-label="Attach image"
                  title={selectedModelSupportsImages ? "Attach image" : "Selected model does not support image inputs"}
                  onClick={() => attachmentInputRef.current?.click()}
                >
                  +
                </button>
                <ModelPicker
                  models={session?.models ?? []}
                  selectedIndex={session?.selected_model_index ?? 0}
                  disabled={controlsDisabled}
                  onChange={selectModel}
                />
                <WorkModeSwitcher
                  workMode={session?.work_mode ?? "off"}
                  disabled={controlsDisabled}
                  onChange={setWorkMode}
                />
                <ModeSwitcher
                  mode={session?.mode ?? "note"}
                  disabled={controlsDisabled}
                  onChange={setMode}
                />
                <CmdsMenu disabled={controlsDisabled} />
              </div>
              <SendCancelControls
                runActive={runActive}
                submitting={submitting}
                canSubmit={canSubmit}
                onSubmit={sendPrompt}
                onCancel={cancelRun}
              />
            </div>
          </>
        )}
      </div>
    </section>
  );
}

interface ContextMeterState {
  ariaLabel: string;
  title: string;
  style: CSSProperties & { "--context-fill": string };
}

function contextMeterState(
  session: SessionStatePayload | null,
  currentDoc: string,
): ContextMeterState {
  const usage = session?.context_usage;
  const contextWindow = usage?.context_window ?? session?.selected_model.context_window ?? null;
  const baseTokens = usage?.tokens_used ?? null;
  const lastNoteChars = usage?.last_note_chars ?? null;
  let effectiveTokens = baseTokens;
  let estimate = false;

  if (baseTokens !== null && lastNoteChars !== null) {
    effectiveTokens = Math.max(0, baseTokens + Math.floor((currentDoc.length - lastNoteChars) / 4));
    estimate = currentDoc.length !== lastNoteChars;
  }

  const fraction =
    effectiveTokens !== null && contextWindow !== null && contextWindow > 0
      ? Math.min(1, Math.max(0, effectiveTokens / contextWindow))
      : null;
  const fillPercent = fraction === null ? "0%" : `${(fraction * 100).toFixed(2)}%`;
  const tokenLabel =
    effectiveTokens !== null && contextWindow !== null
      ? `${estimate ? "~" : ""}${effectiveTokens.toLocaleString()} / ${contextWindow.toLocaleString()} tokens`
      : "Context window unknown";

  return {
    ariaLabel:
      fraction === null
        ? "Context usage unknown"
        : `Context usage ${tokenLabel} (${Math.round(fraction * 100)}%)`,
    title: tokenLabel,
    style: { "--context-fill": fillPercent },
  };
}

function isWsRequestError(error: unknown): error is WsRequestError {
  return (
    error instanceof Error &&
    "reason" in error &&
    typeof (error as WsRequestError).reason === "string"
  );
}

function formatControlError(error: unknown): string {
  if (isWsRequestError(error)) {
    return `${error.reason}${error.details ? ` ${JSON.stringify(error.details)}` : ""}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

const SUPPORTED_IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif"]);

function isSupportedImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) {
    return true;
  }
  const dotIndex = file.name.lastIndexOf(".");
  const extension = dotIndex >= 0 ? file.name.slice(dotIndex).toLowerCase() : "";
  return SUPPORTED_IMAGE_EXTENSIONS.has(extension);
}

async function fileToPromptAttachment(file: File) {
  const name = file.name || fallbackImageName(file.type);
  return {
    id: createAttachmentId(name),
    name,
    data_base64: await readFileAsBase64(file),
    size_bytes: Number.isFinite(file.size) ? file.size : null,
  };
}

function imageFilesFromClipboard(data: DataTransfer): File[] {
  const files: File[] = [];
  const addFile = (file: File | null) => {
    if (!file || !isSupportedImageFile(file)) {
      return;
    }
    const key = `${file.name}:${file.type}:${file.size}:${file.lastModified}`;
    const alreadyAdded = files.some(
      (existing) =>
        `${existing.name}:${existing.type}:${existing.size}:${existing.lastModified}` === key,
    );
    if (alreadyAdded) {
      return;
    }
    files.push(file);
  };

  for (const item of Array.from(data.items)) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      addFile(item.getAsFile());
    }
  }
  for (const file of Array.from(data.files)) {
    addFile(file);
  }
  return files;
}

function fallbackImageName(mimeType: string): string {
  if (mimeType === "image/jpeg") {
    return "pasted-image.jpg";
  }
  const subtype = mimeType.startsWith("image/") ? mimeType.slice("image/".length) : "png";
  return `pasted-image.${subtype || "png"}`;
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error(`Could not read ${file.name}`));
    reader.onload = () => {
      const result = reader.result;
      if (typeof result !== "string") {
        reject(new Error(`Could not read ${file.name}`));
        return;
      }
      const commaIndex = result.indexOf(",");
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

function createAttachmentId(name: string): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}-${name}`;
}
