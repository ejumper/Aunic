import type { TranscriptRowPayload } from "../../../ws/types";
import { RowShell } from "./RowShell";
import { asRecord, commandFromRows, stringValue } from "./rowUtils";

interface BashRowProps {
  row: TranscriptRowPayload;
  toolCall?: TranscriptRowPayload;
  expanded: boolean;
  onToggle: (rowNumber: number) => void;
  onDelete: (rowNumber: number) => void;
}

export function BashRow({ row, toolCall, expanded, onToggle, onDelete }: BashRowProps) {
  const payload = asRecord(row.content);
  const command = commandFromRows(row, toolCall) || "(no command)";
  const stdout = stringValue(payload.stdout) || (typeof row.content === "string" ? row.content : "");
  const stderr = stringValue(payload.stderr);
  const exitCode = payload.exit_code;

  return (
    <RowShell
      row={row}
      label="bash"
      isError={row.type === "tool_error"}
      onDelete={onDelete}
      mid={<code className="tr-command">$ {firstLine(command)}</code>}
      end={
        <button
          type="button"
          className="tr-toggle"
          aria-expanded={expanded}
          onClick={() => onToggle(row.row_number)}
        >
          {expanded ? "[ ^ ]" : "[ v ]"}
        </button>
      }
      detail={
        expanded ? (
          <div className="tr-bash-detail">
            <pre className="tr-bash-command">$ {command}</pre>
            {stdout ? <OutputBlock label="stdout" text={stdout} /> : null}
            {stderr ? <OutputBlock label="stderr" text={stderr} tone="error" /> : null}
            {exitCode !== undefined ? (
              <span
                className={
                  exitCode === 0 ? "tr-exit-code" : "tr-exit-code tr-exit-code--error"
                }
              >
                exit {String(exitCode)}
              </span>
            ) : null}
          </div>
        ) : null
      }
    />
  );
}

function OutputBlock({ label, text, tone }: { label: string; text: string; tone?: "error" }) {
  return (
    <div className="tr-bash-output-block">
      <span className="tr-bash-label">{label}</span>
      <pre className={`tr-bash-output${tone === "error" ? " tr-bash-output--error" : ""}`}>
        {text}
      </pre>
    </div>
  );
}

function firstLine(text: string): string {
  return text.split("\n")[0] ?? text;
}
