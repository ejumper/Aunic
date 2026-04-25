import type { IndicatorMessage } from "../../state/session";
import type { PromptImageAttachment } from "../../state/prompt";
import type { SessionStatePayload } from "../../ws/types";

interface IndicatorLineProps {
  session: SessionStatePayload | null;
  indicator: IndicatorMessage | null;
  attachments: PromptImageAttachment[];
  onRemoveAttachment: (id: string) => void;
}

export function IndicatorLine({
  session,
  indicator,
  attachments,
  onRemoveAttachment,
}: IndicatorLineProps) {
  const mode = session?.mode ?? "note";
  const workMode = session?.work_mode ?? "off";
  const model = session?.selected_model.label ?? "No model";
  const status = indicator?.text ?? "Idle.";
  const kind = indicator?.kind === "error" || indicator?.kind === "tool_error" ? "error" : "status";

  return (
    <div className={`prompt-indicator prompt-indicator--${kind}`}>
      <p className="prompt-indicator__text">
        <span>{status}</span>
        <span aria-hidden="true"> · </span>
        <span>{mode}</span>
        <span aria-hidden="true"> / </span>
        <span>{workMode}</span>
        <span aria-hidden="true"> / </span>
        <span>{model}</span>
      </p>
      {attachments.length > 0 ? (
        <div className="prompt-indicator__attachments" aria-label="Prompt image attachments">
          {attachments.map((attachment) => (
            <span key={attachment.id} className="prompt-attachment-chip">
              <button
                type="button"
                className="prompt-attachment-chip__remove"
                aria-label={`Remove ${attachment.name}`}
                onClick={() => onRemoveAttachment(attachment.id)}
              >
                x
              </button>
              <span className="prompt-attachment-chip__name" title={attachment.name}>
                {attachment.name}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
