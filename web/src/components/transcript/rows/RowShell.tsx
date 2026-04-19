import type { ReactNode } from "react";
import type { TranscriptRowPayload } from "../../../ws/types";

interface RowShellProps {
  row: TranscriptRowPayload;
  label: string;
  isError?: boolean;
  mid: ReactNode;
  end?: ReactNode;
  detail?: ReactNode;
  onDelete: (rowNumber: number) => void;
}

export function RowShell({ row, label, isError, mid, end, detail, onDelete }: RowShellProps) {
  return (
    <div
      className={`tr${isError ? " tr--error" : ""}`}
      data-row-number={row.row_number}
    >
      <div className="tr__main">
        <button
          type="button"
          className="tr__del"
          aria-label={`Delete transcript row ${row.row_number}`}
          onClick={() => onDelete(row.row_number)}
        >
          ✕
        </button>
        <span className={`tr__label${isError ? " tr__label--error" : ""}`}>{label}</span>
        <div className="tr__mid">{mid}</div>
        {end != null ? <div className="tr__end">{end}</div> : null}
      </div>
      {detail != null ? <div className="tr__detail">{detail}</div> : null}
    </div>
  );
}
