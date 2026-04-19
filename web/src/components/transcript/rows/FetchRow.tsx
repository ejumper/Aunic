import type { TranscriptRowPayload } from "../../../ws/types";
import { RowShell } from "./RowShell";
import { asRecord, normalizedHost, stringValue } from "./rowUtils";

interface FetchRowProps {
  row: TranscriptRowPayload;
  onDelete: (rowNumber: number) => void;
}

export function FetchRow({ row, onDelete }: FetchRowProps) {
  const payload = asRecord(row.content);
  const title = stringValue(payload.title) || stringValue(payload.url) || "(no title)";
  const url = stringValue(payload.url);
  const snippet = stringValue(payload.snippet) || stringValue(payload.text);
  const domain = normalizedHost(url);

  return (
    <RowShell
      row={row}
      label={row.type === "tool_error" ? "fetch error" : "Fetch"}
      isError={row.type === "tool_error"}
      onDelete={onDelete}
      mid={
        <>
          <span className="tr-fetch-domain">{domain}</span>
          {url ? (
            <a href={url} target="_blank" rel="noreferrer" className="tr-fetch-title">
              {title}
            </a>
          ) : (
            <span className="tr-fetch-title">{title}</span>
          )}
        </>
      }
      end={
        url ? (
          <a href={url} target="_blank" rel="noreferrer" className="tr-link">
            ↗
          </a>
        ) : null
      }
      detail={snippet ? <pre className="tr-snippet">{snippet}</pre> : null}
    />
  );
}
