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
  const isRagSearch = row.tool_name === "rag_search";
  const query = queryFromRows(row, toolCall) || "(no query)";
  const results = searchResults(row.content);

  return (
    <RowShell
      row={row}
      label={
        row.type === "tool_error"
          ? isRagSearch
            ? "RAG error"
            : "search error"
          : isRagSearch
            ? "RAG"
            : "Search"
      }
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
                return isRagSearch ? (
                  <RagSearchResultRow
                    key={resultKey(result, index)}
                    result={result}
                    index={index}
                    rowNumber={row.row_number}
                    onDeleteResult={onDeleteResult}
                  />
                ) : (
                  <WebSearchResultRow
                    key={resultKey(result, index)}
                    result={result}
                    index={index}
                    rowNumber={row.row_number}
                    onDeleteResult={onDeleteResult}
                  />
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

interface SearchResultRowProps {
  result: Record<string, unknown>;
  index: number;
  rowNumber: number;
  onDeleteResult: (rowNumber: number, resultIndex: number) => void;
}

function WebSearchResultRow({
  result,
  index,
  rowNumber,
  onDeleteResult,
}: SearchResultRowProps) {
  const title = stringValue(result.title) || "(no title)";
  const url = stringValue(result.url);
  const snippet = stringValue(result.snippet);

  return (
    <div className="tr-search-result">
      <button
        type="button"
        className="tr__del"
        aria-label={`Delete search result ${index + 1}`}
        onClick={() => onDeleteResult(rowNumber, index)}
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
        {snippet ? <span className="tr-search-snippet"> — {snippet}</span> : null}
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
}

function RagSearchResultRow({
  result,
  index,
  rowNumber,
  onDeleteResult,
}: SearchResultRowProps) {
  const title = stringValue(result.title) || "(no title)";
  const url = stringValue(result.url);
  const source = stringValue(result.source) || (url ? normalizedHost(url) : "RAG");
  const heading = lastHeading(result);
  const snippet = stringValue(result.snippet);
  const reference = ragReference(result);

  return (
    <div className="tr-search-result tr-search-result--rag">
      <button
        type="button"
        className="tr__del"
        aria-label={`Delete search result ${index + 1}`}
        onClick={() => onDeleteResult(rowNumber, index)}
      >
        ✕
      </button>
      <span className="tr-search-num">{index + 1}</span>
      <span className="tr-search-domain">{source}</span>
      <div className="tr-search-body">
        {url ? (
          <a href={url} target="_blank" rel="noreferrer" className="tr-search-title">
            {title}
          </a>
        ) : (
          <span className="tr-search-title tr-search-title--static">{title}</span>
        )}
        {heading ? <div className="tr-search-meta"># {heading}</div> : null}
        {snippet ? <div className="tr-search-snippet tr-search-snippet--block">{snippet}</div> : null}
        {reference ? <div className="tr-search-path">{reference}</div> : null}
      </div>
      {url ? (
        <a href={url} target="_blank" rel="noreferrer" className="tr-link">
          ↗
        </a>
      ) : (
        <span />
      )}
    </div>
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

function lastHeading(result: Record<string, unknown>): string {
  const headings = result.heading_path;
  if (!Array.isArray(headings)) {
    return "";
  }
  for (let index = headings.length - 1; index >= 0; index -= 1) {
    const heading = headings[index];
    if (typeof heading === "string" && heading.trim()) {
      return heading.trim();
    }
  }
  return "";
}

function ragReference(result: Record<string, unknown>): string {
  const localPath = stringValue(result.local_path);
  if (localPath) {
    return localPath;
  }
  const url = stringValue(result.url);
  if (url) {
    return url;
  }
  const source = stringValue(result.source);
  const resultId = stringValue(result.result_id);
  const docId = stringValue(result.doc_id);
  const identifier = resultId || docId;
  if (!identifier) {
    return "";
  }
  return source ? `[${source}] ${identifier}` : identifier;
}

function resultKey(result: Record<string, unknown>, index: number): string {
  return [
    index,
    stringValue(result.result_id),
    stringValue(result.url),
    stringValue(result.local_path),
    stringValue(result.title),
  ].join(":");
}
