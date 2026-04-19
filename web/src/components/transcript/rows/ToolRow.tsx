import type { TranscriptRowPayload } from "../../../ws/types";
import { RowShell } from "./RowShell";
import { flattenToolResult } from "./rowUtils";

interface ToolRowProps {
  row: TranscriptRowPayload;
  expanded: boolean;
  onToggle: (rowNumber: number) => void;
  onDelete: (rowNumber: number) => void;
}

const COLLAPSE_LINES = 3;
const COLLAPSE_CHARS = 200;

export function ToolRow({ row, expanded, onToggle, onDelete }: ToolRowProps) {
  const text = flattenToolResult(row.tool_name, row.type, row.content);
  const lines = text.split("\n");
  const collapsible = lines.length > COLLAPSE_LINES || text.length > COLLAPSE_CHARS;
  const previewText = collapsible ? lines[0] + " …" : text;

  return (
    <RowShell
      row={row}
      label={row.tool_name ?? "tool"}
      isError={row.type === "tool_error"}
      onDelete={onDelete}
      mid={<span className="tr-text-preview">{previewText}</span>}
      end={
        collapsible ? (
          <button
            type="button"
            className="tr-toggle"
            aria-expanded={expanded}
            onClick={() => onToggle(row.row_number)}
          >
            {expanded ? "^" : "v"}
          </button>
        ) : null
      }
      detail={expanded ? <pre className="tr-tool-text">{text}</pre> : null}
    />
  );
}
