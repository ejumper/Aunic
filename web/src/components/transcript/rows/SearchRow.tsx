import type { TranscriptRowPayload } from "../../../ws/types";
import { RowShell } from "./RowShell";
import { asRecord, normalizedHost, queryFromRows, stringValue } from "./rowUtils";

interface SearchRowProps {
  row: TranscriptRowPayload;
  toolCall?: TranscriptRowPayload;
  expanded: boolean;
  onToggle: (rowNumber: number) => void;
  onDelete: (rowNumber: number) => void;
  onDeleteResult: (rowNumber: number, resultIndex: number) => void;
}

interface SearchResultItem {
  result: Record<string, unknown>;
  index: number;
}

export function SearchRow({
  row,
  toolCall,
  expanded,
  onToggle,
  onDelete,
  onDeleteResult,
}: SearchRowProps) {
  const query = queryFromRows(row, toolCall) || "(no query)";
  const results = searchResults(row.content);

  return (
    <RowShell
      row={row}
      label={row.type === "tool_error" ? "search error" : "Search"}
      isError={row.type === "tool_error"}
      onDelete={onDelete}
      mid={
        <span className="tr-text-preview">
          {results.length} result{results.length === 1 ? "" : "s"} · {query}
        </span>
      }
      end={
        <button
          type="button"
          className="tr-toggle"
          aria-expanded={expanded}
          onClick={() => onToggle(row.row_number)}
        >
          {expanded ? "^" : "v"}
        </button>
      }
      detail={
        expanded ? (
          results.length > 0 ? (
            <div className="tr-search-results">
              {results.map(({ result, index }) => {
                const title = stringValue(result.title) || "(no title)";
                const url = stringValue(result.url);
                const snippet = stringValue(result.snippet);
                return (
                  <div key={`${index}:${url}:${title}`} className="tr-search-result">
                    <button
                      type="button"
                      className="tr__del"
                      aria-label={`Delete search result ${index + 1}`}
                      onClick={() => onDeleteResult(row.row_number, index)}
                    >
                      ✕
                    </button>
                    <span className="tr-search-num">{index + 1}</span>
                    <span className="tr-search-domain">{normalizedHost(url)}</span>
                    <a
                      href={url || undefined}
                      target="_blank"
                      rel="noreferrer"
                      className="tr-search-title"
                    >
                      {title}
                      {snippet ? (
                        <span className="tr-search-snippet"> — {snippet}</span>
                      ) : null}
                    </a>
                    {url ? (
                      <a href={url} target="_blank" rel="noreferrer" className="tr-link">
                        ↗
                      </a>
                    ) : (
                      <span />
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="tr-empty">No results.</p>
          )
        ) : null
      }
    />
  );
}

function searchResults(content: unknown): SearchResultItem[] {
  const rawResults = Array.isArray(content) ? content : asRecord(content).results;
  if (!Array.isArray(rawResults)) {
    return [];
  }
  return rawResults
    .map((result, index) => ({ result, index }))
    .filter((item): item is SearchResultItem => {
      return item.result && typeof item.result === "object" && !Array.isArray(item.result);
    });
}
