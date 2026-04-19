import type { IndicatorMessage } from "../../state/session";
import type { SessionStatePayload } from "../../ws/types";

interface IndicatorLineProps {
  session: SessionStatePayload | null;
  indicator: IndicatorMessage | null;
}

export function IndicatorLine({ session, indicator }: IndicatorLineProps) {
  const mode = session?.mode ?? "note";
  const workMode = session?.work_mode ?? "off";
  const model = session?.selected_model.label ?? "No model";
  const status = indicator?.text ?? "Idle.";
  const kind = indicator?.kind === "error" || indicator?.kind === "tool_error" ? "error" : "status";

  return (
    <p className={`prompt-indicator prompt-indicator--${kind}`}>
      <span>{status}</span>
      <span aria-hidden="true"> · </span>
      <span>{mode}</span>
      <span aria-hidden="true"> / </span>
      <span>{workMode}</span>
      <span aria-hidden="true"> / </span>
      <span>{model}</span>
    </p>
  );
}
