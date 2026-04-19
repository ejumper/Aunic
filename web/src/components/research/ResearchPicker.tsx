import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { useNoteEditorStore } from "../../state/noteEditor";
import { useSessionStore } from "../../state/session";
import { useTranscriptStore } from "../../state/transcript";
import type { WsRequestError } from "../../ws/client";
import type { WsClient } from "../../ws/client";
import type {
  FileSnapshotPayload,
  ResearchChunkPayload,
  ResearchResultPayload,
  ResearchStatePayload,
} from "../../ws/types";

interface ResearchPickerProps {
  client: WsClient;
  activeFile: string;
  research: ResearchStatePayload;
}

export function ResearchPicker({ client, activeFile, research }: ResearchPickerProps) {
  const setIndicatorMessage = useSessionStore((store) => store.setIndicatorMessage);
  const pickerRef = useRef<HTMLElement | null>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [selectedResult, setSelectedResult] = useState<number | null>(null);
  const [expandedResults, setExpandedResults] = useState<Set<number>>(new Set());
  const [selectedChunks, setSelectedChunks] = useState<Set<number>>(new Set());
  const [expandedChunks, setExpandedChunks] = useState<Set<number>>(new Set());

  const resultCount = research.results.length;
  const chunks = research.packet?.chunks ?? [];
  const chunkFocusMax = Math.max(-1, chunks.length - 1);
  const busy = research.busy !== null;
  const sourceLabel = research.source === "rag" ? "RAG" : "Web";

  useEffect(() => {
    setFocusedIndex(research.mode === "chunks" ? -1 : 0);
    setSelectedResult(null);
    setSelectedChunks(new Set());
    setExpandedResults(new Set());
    setExpandedChunks(new Set());
  }, [research.mode, research.query, research.source]);

  useEffect(() => {
    pickerRef.current?.focus();
  }, [research.mode, research.query, research.source]);

  const fetchSelected = useCallback(
    async (index: number | null = selectedResult) => {
      const targetIndex = index ?? selectedResult;
      if (targetIndex === null || busy) {
        return;
      }
      try {
        const snapshot = await client.request("research_fetch_result", {
          active_file: activeFile,
          result_index: targetIndex,
        });
        applySnapshot(snapshot, "Fetched research result.");
        setIndicatorMessage("Fetched research result.");
      } catch (error) {
        setIndicatorMessage(formatResearchError(error), "error");
      }
    },
    [activeFile, busy, client, selectedResult, setIndicatorMessage],
  );

  const insertChunks = useCallback(
    async (mode: "selected_chunks" | "full_page") => {
      if (busy) {
        return;
      }
      if (mode === "selected_chunks" && selectedChunks.size === 0) {
        setIndicatorMessage("Select chunks before inserting.", "error");
        return;
      }
      try {
        const snapshot = await client.request("research_insert_chunks", {
          active_file: activeFile,
          mode,
          chunk_indices:
            mode === "selected_chunks" ? [...selectedChunks].sort((a, b) => a - b) : undefined,
        });
        applySnapshot(snapshot, "Inserted research content.");
        setIndicatorMessage("Inserted research content.");
      } catch (error) {
        setIndicatorMessage(formatResearchError(error), "error");
      }
    },
    [activeFile, busy, client, selectedChunks, setIndicatorMessage],
  );

  const back = useCallback(async () => {
    try {
      await client.request("research_back", {});
      setIndicatorMessage(`Back to ${sourceLabel.toLowerCase()} results.`);
    } catch (error) {
      setIndicatorMessage(formatResearchError(error), "error");
    }
  }, [client, setIndicatorMessage, sourceLabel]);

  const cancel = useCallback(async () => {
    try {
      await client.request("research_cancel", {});
      setIndicatorMessage(`${sourceLabel} search cancelled.`);
    } catch (error) {
      setIndicatorMessage(formatResearchError(error), "error");
    }
  }, [client, setIndicatorMessage, sourceLabel]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (research.mode === "results") {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setFocusedIndex((current) => Math.min(resultCount - 1, current + 1));
          return;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setFocusedIndex((current) => Math.max(0, current - 1));
          return;
        }
        if (event.key === "ArrowRight") {
          event.preventDefault();
          if (focusedIndex >= 0 && focusedIndex < resultCount) {
            setExpandedResults((current) => addSetItem(current, focusedIndex));
          }
          return;
        }
        if (event.key === "ArrowLeft") {
          event.preventDefault();
          if (focusedIndex >= 0 && focusedIndex < resultCount) {
            setExpandedResults((current) => removeSetItem(current, focusedIndex));
          }
          return;
        }
        if (event.key === " ") {
          event.preventDefault();
          setSelectedResult((current) => (current === focusedIndex ? null : focusedIndex));
          return;
        }
        if (event.key === "Enter") {
          event.preventDefault();
          void fetchSelected(selectedResult ?? focusedIndex);
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          void cancel();
        }
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        setFocusedIndex((current) => Math.min(chunkFocusMax, current + 1));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setFocusedIndex((current) => Math.max(-1, current - 1));
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        if (focusedIndex >= 0 && focusedIndex < chunks.length) {
          setExpandedChunks((current) => addSetItem(current, focusedIndex));
        }
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        if (focusedIndex >= 0 && focusedIndex < chunks.length) {
          setExpandedChunks((current) => removeSetItem(current, focusedIndex));
        }
        return;
      }
      if (event.key === " ") {
        event.preventDefault();
        if (focusedIndex >= 0) {
          setSelectedChunks((current) => toggleSetItem(current, focusedIndex));
        }
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        if (focusedIndex === -1) {
          void insertChunks("full_page");
          return;
        }
        if (selectedChunks.size > 0) {
          void insertChunks("selected_chunks");
          return;
        }
        if (focusedIndex >= 0) {
          setSelectedChunks((current) => toggleSetItem(current, focusedIndex));
        }
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        void back();
      }
    },
    [
      back,
      cancel,
      chunkFocusMax,
      chunks.length,
      fetchSelected,
      focusedIndex,
      insertChunks,
      research.mode,
      resultCount,
      selectedChunks.size,
      selectedResult,
    ],
  );

  const subtitle = useMemo(() => {
    if (research.mode === "results") {
      return `${resultCount} result${resultCount === 1 ? "" : "s"} for "${research.query}"`;
    }
    const total = research.packet?.total_chunks;
    if (total && total > chunks.length) {
      return `${chunks.length} of ${total} chunks from "${research.packet?.title ?? ""}"`;
    }
    return `${chunks.length} chunk${chunks.length === 1 ? "" : "s"} from "${
      research.packet?.title ?? ""
    }"`;
  }, [chunks.length, research.mode, research.packet, research.query, resultCount]);

  return (
    <section
      ref={pickerRef}
      className="research-picker"
      aria-label={`${sourceLabel} research picker`}
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      <div className="research-picker__header">
        <div className="research-picker__heading">
          <strong>{sourceLabel}</strong>
          <span>{subtitle}</span>
        </div>
        <div className="research-picker__actions">
          {research.mode === "chunks" ? (
            <button type="button" className="mode-pill" disabled={busy} onClick={() => void back()}>
              Back
            </button>
          ) : null}
          <button type="button" className="mode-pill" disabled={busy} onClick={() => void cancel()}>
            Cancel
          </button>
        </div>
      </div>

      {research.mode === "results" ? (
        <ResultsView
          results={research.results}
          focusedIndex={focusedIndex}
          selectedResult={selectedResult}
          expandedResults={expandedResults}
          busy={busy}
          onFocus={setFocusedIndex}
          onSelect={(index) => setSelectedResult((current) => (current === index ? null : index))}
          onToggleExpand={(index) => setExpandedResults((current) => toggleSetItem(current, index))}
          onFetch={() => void fetchSelected()}
        />
      ) : (
        <ChunksView
          chunks={chunks}
          focusedIndex={focusedIndex}
          selectedChunks={selectedChunks}
          expandedChunks={expandedChunks}
          fullTextAvailable={research.packet?.full_text_available ?? false}
          busy={busy}
          onFocus={setFocusedIndex}
          onSelectChunk={(index) => setSelectedChunks((current) => toggleSetItem(current, index))}
          onToggleExpand={(index) => setExpandedChunks((current) => toggleSetItem(current, index))}
          onInsertSelected={() => void insertChunks("selected_chunks")}
          onInsertFullPage={() => void insertChunks("full_page")}
        />
      )}
    </section>
  );
}

interface ResultsViewProps {
  results: ResearchResultPayload[];
  focusedIndex: number;
  selectedResult: number | null;
  expandedResults: Set<number>;
  busy: boolean;
  onFocus: (index: number) => void;
  onSelect: (index: number) => void;
  onToggleExpand: (index: number) => void;
  onFetch: () => void;
}

function ResultsView({
  results,
  focusedIndex,
  selectedResult,
  expandedResults,
  busy,
  onFocus,
  onSelect,
  onToggleExpand,
  onFetch,
}: ResultsViewProps) {
  return (
    <>
      <div className="research-picker__list" role="listbox" aria-label="Research results">
        {results.length === 0 ? <p className="muted research-picker__empty">No results.</p> : null}
        {results.map((result, index) => {
          const expanded = expandedResults.has(index);
          const selected = selectedResult === index;
          return (
            <article
              key={`${result.result_id ?? result.url ?? result.title}-${index}`}
              className={`research-row ${focusedIndex === index ? "research-row--focused" : ""} ${
                selected ? "research-row--selected" : ""
              }`}
              role="option"
              aria-selected={selected}
              onClick={() => onFocus(index)}
            >
              <label className="research-row__check">
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => onSelect(index)}
                  aria-label={`Select result ${index + 1}`}
                />
              </label>
              <div className="research-row__body">
                <div className="research-row__title">{result.title || "(no title)"}</div>
                <div className="research-row__meta">
                  {result.url ?? result.local_path ?? result.source ?? result.result_id}
                </div>
                <p className="research-row__snippet">
                  {expanded ? result.snippet : truncateText(result.snippet, 220)}
                </p>
                <div className="research-row__controls">
                  <button type="button" className="text-button" onClick={() => onToggleExpand(index)}>
                    {expanded ? "Less" : "More"}
                  </button>
                  {result.url ? (
                    <a className="text-button" href={result.url} target="_blank" rel="noreferrer">
                      Open
                    </a>
                  ) : null}
                </div>
              </div>
            </article>
          );
        })}
      </div>
      <div className="research-picker__footer">
        <span className="muted">Space selects. Enter fetches. Escape cancels.</span>
        <button
          type="button"
          className="prompt-send-button"
          disabled={busy || selectedResult === null}
          onClick={onFetch}
        >
          Fetch
        </button>
      </div>
    </>
  );
}

interface ChunksViewProps {
  chunks: ResearchChunkPayload[];
  focusedIndex: number;
  selectedChunks: Set<number>;
  expandedChunks: Set<number>;
  fullTextAvailable: boolean;
  busy: boolean;
  onFocus: (index: number) => void;
  onSelectChunk: (index: number) => void;
  onToggleExpand: (index: number) => void;
  onInsertSelected: () => void;
  onInsertFullPage: () => void;
}

function ChunksView({
  chunks,
  focusedIndex,
  selectedChunks,
  expandedChunks,
  fullTextAvailable,
  busy,
  onFocus,
  onSelectChunk,
  onToggleExpand,
  onInsertSelected,
  onInsertFullPage,
}: ChunksViewProps) {
  return (
    <>
      <div className="research-picker__list" role="listbox" aria-label="Fetched chunks">
        <button
          type="button"
          className={`research-full-page ${focusedIndex === -1 ? "research-row--focused" : ""}`}
          disabled={busy || !fullTextAvailable}
          onClick={onInsertFullPage}
          onFocus={() => onFocus(-1)}
        >
          Insert full page
        </button>
        {chunks.map((chunk, index) => {
          const expanded = expandedChunks.has(index);
          const selected = selectedChunks.has(index);
          const heading = chunk.heading_path.at(-1) ?? chunk.title;
          return (
            <article
              key={`${chunk.chunk_id || chunk.title}-${index}`}
              className={`research-row ${focusedIndex === index ? "research-row--focused" : ""} ${
                selected ? "research-row--selected" : ""
              } ${chunk.is_match ? "research-row--match" : ""}`}
              role="option"
              aria-selected={selected}
              onClick={() => onFocus(index)}
            >
              <label className="research-row__check">
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => onSelectChunk(index)}
                  aria-label={`Select chunk ${index + 1}`}
                />
              </label>
              <div className="research-row__body">
                <div className="research-row__title">{heading || `Chunk ${index + 1}`}</div>
                {chunk.heading_path.length > 1 ? (
                  <div className="research-row__meta">{chunk.heading_path.join(" > ")}</div>
                ) : null}
                <p className="research-row__snippet">
                  {expanded ? chunk.text : truncateText(chunk.text, 360)}
                </p>
                <button type="button" className="text-button" onClick={() => onToggleExpand(index)}>
                  {expanded ? "Less" : "More"}
                </button>
              </div>
            </article>
          );
        })}
      </div>
      <div className="research-picker__footer">
        <span className="muted">Space selects chunks. Enter inserts. Escape goes back.</span>
        <button
          type="button"
          className="prompt-send-button"
          disabled={busy || selectedChunks.size === 0}
          onClick={onInsertSelected}
        >
          Insert selected
        </button>
      </div>
    </>
  );
}

function toggleSetItem(items: Set<number>, item: number): Set<number> {
  const next = new Set(items);
  if (next.has(item)) {
    next.delete(item);
  } else {
    next.add(item);
  }
  return next;
}

function addSetItem(items: Set<number>, item: number): Set<number> {
  if (items.has(item)) {
    return items;
  }
  const next = new Set(items);
  next.add(item);
  return next;
}

function removeSetItem(items: Set<number>, item: number): Set<number> {
  if (!items.has(item)) {
    return items;
  }
  const next = new Set(items);
  next.delete(item);
  return next;
}

function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength).trimEnd()}...`;
}

function applySnapshot(snapshot: FileSnapshotPayload, notice: string): void {
  useNoteEditorStore.getState().applySnapshot(snapshot, {
    preserveCurrentDocIfDirty: true,
    remountEditor: false,
    notice,
  });
  useTranscriptStore.getState().loadFromSnapshot(snapshot);
}

function isWsRequestError(error: unknown): error is WsRequestError {
  return (
    error instanceof Error &&
    "reason" in error &&
    typeof (error as WsRequestError).reason === "string"
  );
}

function formatResearchError(error: unknown): string {
  if (isWsRequestError(error)) {
    return `${error.reason}${error.details ? ` ${JSON.stringify(error.details)}` : ""}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
