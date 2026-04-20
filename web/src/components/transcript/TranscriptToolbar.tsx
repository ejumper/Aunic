import type { TranscriptFilter, TranscriptSortOrder } from "../../state/transcript";

interface TranscriptToolbarProps {
  open: boolean;
  maximized: boolean;
  mobileDockMode?: boolean;
  filterMode: TranscriptFilter;
  sortOrder: TranscriptSortOrder;
  hasRows: boolean;
  onToggleOpen: () => void;
  onToggleMaximized: () => void;
  onFilter: (mode: TranscriptFilter) => void;
  onToggleSort: () => void;
}

const FILTERS: Array<{ mode: Exclude<TranscriptFilter, "all">; label: string }> = [
  { mode: "chat", label: "Chat" },
  { mode: "tools", label: "Tools" },
  { mode: "search", label: "Search" },
];

export function TranscriptToolbar({
  open,
  maximized,
  mobileDockMode = false,
  filterMode,
  sortOrder,
  hasRows,
  onToggleOpen,
  onToggleMaximized,
  onFilter,
  onToggleSort,
}: TranscriptToolbarProps) {
  if (!open) {
    return (
      <div className="transcript-toolbar transcript-toolbar--collapsed">
        <button type="button" className="secondary-button" onClick={onToggleOpen}>
          ^
        </button>
        <span className="muted transcript-collapsed-label">
          {hasRows ? "Transcript collapsed." : "No transcript yet."}
        </span>
      </div>
    );
  }

  return (
    <div className="transcript-toolbar">
      <div className="transcript-toolbar__controls" aria-label="Transcript controls">
        <button type="button" className="secondary-button" onClick={onToggleOpen}>
          v
        </button>
        {!mobileDockMode ? (
          <button type="button" className="secondary-button" onClick={onToggleMaximized}>
            {maximized ? "-" : "+"}
          </button>
        ) : null}
        {FILTERS.map(({ mode, label }) => {
          const active = filterMode === mode;
          return (
            <button
              key={mode}
              type="button"
              className={active ? "transcript-filter transcript-filter-active" : "transcript-filter"}
              aria-pressed={active}
              onClick={() => onFilter(active ? "all" : mode)}
            >
              {label}
            </button>
          );
        })}
        <button type="button" className="secondary-button" onClick={onToggleSort}>
          {sortOrder === "descending" ? "Descending" : "Ascending"}
        </button>
      </div>
    </div>
  );
}
