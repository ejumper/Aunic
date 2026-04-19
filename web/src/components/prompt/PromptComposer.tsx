import { useCallback, type CSSProperties } from "react";
import { useExplorerStore } from "../../state/explorer";
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
import { PromptEditor } from "./PromptEditor";
import { SendCancelControls } from "./SendCancelControls";
import { WorkModeSwitcher } from "./WorkModeSwitcher";
import { CmdsMenu } from "./CmdsMenu";

export function PromptComposer() {
  const { client } = useWs();
  const openFile = useExplorerStore((store) => store.openFile);
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
  const setDraft = usePromptStore((store) => store.setDraft);
  const submit = usePromptStore((store) => store.submit);
  const cancel = usePromptStore((store) => store.cancel);
  const currentDoc = useNoteEditorStore((store) => store.currentDoc);

  const sendPrompt = useCallback(() => {
    void submit(client, openFile, []);
  }, [client, openFile, submit]);

  const cancelRun = useCallback(() => {
    void cancel(client, currentRunId);
  }, [cancel, client, currentRunId]);

  const setMode = useCallback(
    (mode: BrowserMode) => {
      void client.request("set_mode", { mode }).catch((error) => {
        setIndicatorMessage(formatControlError(error), "error");
      });
    },
    [client, setIndicatorMessage],
  );

  const setWorkMode = useCallback(
    (workMode: WorkMode) => {
      void client.request("set_work_mode", { work_mode: workMode }).catch((error) => {
        setIndicatorMessage(formatControlError(error), "error");
      });
    },
    [client, setIndicatorMessage],
  );

  const selectModel = useCallback(
    (index: number) => {
      void client.request("select_model", { index }).catch((error) => {
        setIndicatorMessage(formatControlError(error), "error");
      });
    },
    [client, setIndicatorMessage],
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

  if (!openFile) {
    return null;
  }

  const controlsDisabled = runActive || !session;
  const researchState = session?.research_state;
  const researchActive =
    Boolean(researchState) && researchState?.mode !== undefined && researchState.mode !== "idle";
  const canSubmit =
    !runActive &&
    !submitting &&
    noteStatus !== "loading" &&
    noteStatus !== "saving" &&
    draft.trim().length > 0;
  const contextMeter = contextMeterState(session, currentDoc);

  return (
    <section className="prompt-composer" aria-label="Prompt composer">
      {pendingPermission ? (
        <PermissionPrompt permission={pendingPermission} onResolve={resolvePermission} />
      ) : null}

      <IndicatorLine session={session} indicator={indicatorMessage} />

      <div className="prompt-message-block">
        {researchActive && !pendingPermission && researchState ? (
          <ResearchPicker client={client} activeFile={openFile} research={researchState} />
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
