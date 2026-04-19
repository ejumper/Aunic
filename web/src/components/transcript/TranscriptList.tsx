import type { TranscriptFilter, TranscriptSortOrder } from "../../state/transcript";
import type { TranscriptRowPayload } from "../../ws/types";
import { BashRow } from "./rows/BashRow";
import { ChatRow } from "./rows/ChatRow";
import { FetchRow } from "./rows/FetchRow";
import { SearchRow } from "./rows/SearchRow";
import { ToolRow } from "./rows/ToolRow";

interface TranscriptListProps {
  rows: TranscriptRowPayload[];
  filterMode: TranscriptFilter;
  sortOrder: TranscriptSortOrder;
  expandedRows: Set<number>;
  onToggleExpand: (rowNumber: number) => void;
  onDeleteRow: (rowNumber: number) => void;
  onDeleteSearchResult: (rowNumber: number, resultIndex: number) => void;
}

export function TranscriptList({
  rows,
  filterMode,
  sortOrder,
  expandedRows,
  onToggleExpand,
  onDeleteRow,
  onDeleteSearchResult,
}: TranscriptListProps) {
  const toolCallIndex = new Map(
    rows
      .filter((row) => row.type === "tool_call" && row.tool_id)
      .map((row) => [row.tool_id as string, row]),
  );
  const visibleRows = rows
    .filter((row) => row.type !== "tool_call")
    .filter((row) => rowMatchesFilter(row, filterMode))
    .sort((left, right) =>
      sortOrder === "descending"
        ? right.row_number - left.row_number
        : left.row_number - right.row_number,
    );

  if (visibleRows.length === 0) {
    return <p className="muted transcript-empty">No transcript yet.</p>;
  }

  return (
    <div className="transcript-list" aria-live="polite">
      {visibleRows.map((row) => {
        const toolCall = row.tool_id ? toolCallIndex.get(row.tool_id) : undefined;
        const expanded = expandedRows.has(row.row_number);
        if (row.type === "message") {
          return <ChatRow key={row.row_number} row={row} onDelete={onDeleteRow} />;
        }
        if (row.type === "tool_result" || row.type === "tool_error") {
          if (row.tool_name === "bash") {
            return (
              <BashRow
                key={row.row_number}
                row={row}
                toolCall={toolCall}
                expanded={expanded}
                onToggle={onToggleExpand}
                onDelete={onDeleteRow}
              />
            );
          }
          if (row.tool_name === "web_search") {
            return (
              <SearchRow
                key={row.row_number}
                row={row}
                toolCall={toolCall}
                expanded={expanded}
                onToggle={onToggleExpand}
                onDelete={onDeleteRow}
                onDeleteResult={onDeleteSearchResult}
              />
            );
          }
          if (row.tool_name === "web_fetch") {
            return <FetchRow key={row.row_number} row={row} onDelete={onDeleteRow} />;
          }
          return (
            <ToolRow
              key={row.row_number}
              row={row}
              expanded={expanded}
              onToggle={onToggleExpand}
              onDelete={onDeleteRow}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

function rowMatchesFilter(row: TranscriptRowPayload, filterMode: TranscriptFilter): boolean {
  if (filterMode === "all") {
    return true;
  }
  if (filterMode === "chat") {
    return row.type === "message";
  }
  if (filterMode === "tools") {
    return row.type === "tool_result" || row.type === "tool_error";
  }
  return (
    (row.type === "tool_result" || row.type === "tool_error") &&
    (row.tool_name === "web_search" || row.tool_name === "web_fetch")
  );
}
