import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { useExplorerStore } from "../../state/explorer";
import { useNoteEditorStore } from "../../state/noteEditor";
import { useTranscriptStore } from "../../state/transcript";
import { useWs } from "../../ws/context";
import { TranscriptList } from "./TranscriptList";
import { TranscriptToolbar } from "./TranscriptToolbar";

const PINNED_EDGE_PX = 64;
const MIN_TRANSCRIPT_HEIGHT_PX = 112;
const EDITOR_RESERVE_PX = 160;
const RESIZE_STEP_PX = 16;
const RESIZE_LARGE_STEP_PX = 48;

export function TranscriptPane() {
  const { client } = useWs();
  const openFile = useExplorerStore((store) => store.openFile);
  const noteSnapshot = useNoteEditorStore((store) => store.snapshot);
  const path = useTranscriptStore((store) => store.path);
  const rows = useTranscriptStore((store) => store.rows);
  const filterMode = useTranscriptStore((store) => store.filterMode);
  const sortOrder = useTranscriptStore((store) => store.sortOrder);
  const expandedRows = useTranscriptStore((store) => store.expandedRows);
  const open = useTranscriptStore((store) => store.open);
  const maximized = useTranscriptStore((store) => store.maximized);
  const status = useTranscriptStore((store) => store.status);
  const error = useTranscriptStore((store) => store.error);
  const loadFromSnapshot = useTranscriptStore((store) => store.loadFromSnapshot);
  const reset = useTranscriptStore((store) => store.reset);
  const toggleExpand = useTranscriptStore((store) => store.toggleExpand);
  const setFilter = useTranscriptStore((store) => store.setFilter);
  const toggleSort = useTranscriptStore((store) => store.toggleSort);
  const setOpen = useTranscriptStore((store) => store.setOpen);
  const toggleOpen = useTranscriptStore((store) => store.toggleOpen);
  const toggleMaximized = useTranscriptStore((store) => store.toggleMaximized);
  const deleteRow = useTranscriptStore((store) => store.deleteRow);
  const deleteSearchResult = useTranscriptStore((store) => store.deleteSearchResult);
  const paneRef = useRef<HTMLElement | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const pinnedRef = useRef(true);
  const mobileDefaultClosedPathRef = useRef<string | null>(null);
  const mobileDockMode = useMediaQuery("(max-width: 760px)");
  const [heightPx, setHeightPx] = useState<number | null>(null);

  useEffect(() => {
    if (!openFile) {
      reset();
      return;
    }
    if (noteSnapshot?.path === openFile) {
      loadFromSnapshot(noteSnapshot);
      return;
    }
    if (path !== openFile) {
      reset();
    }
  }, [loadFromSnapshot, noteSnapshot, openFile, path, reset]);

  const handleScroll = useCallback(() => {
    const node = scrollerRef.current;
    if (!node) {
      return;
    }
    if (sortOrder === "descending") {
      pinnedRef.current = node.scrollTop <= PINNED_EDGE_PX;
      return;
    }
    pinnedRef.current = node.scrollHeight - node.scrollTop - node.clientHeight <= PINNED_EDGE_PX;
  }, [sortOrder]);

  const rowEdge = rows.map((row) => row.row_number).join(":");
  useLayoutEffect(() => {
    const node = scrollerRef.current;
    if (!node || !pinnedRef.current) {
      return;
    }
    if (sortOrder === "descending") {
      node.scrollTop = 0;
    } else {
      node.scrollTop = node.scrollHeight;
    }
  }, [rowEdge, sortOrder]);

  useEffect(() => {
    if (!path) {
      mobileDefaultClosedPathRef.current = null;
      return;
    }
    if (!mobileDockMode || mobileDefaultClosedPathRef.current === path) {
      return;
    }
    mobileDefaultClosedPathRef.current = path;
    setOpen(false);
  }, [mobileDockMode, path, setOpen]);

  useEffect(() => {
    if (
      mobileDockMode &&
      open &&
      !maximized &&
      mobileDefaultClosedPathRef.current !== path
    ) {
      toggleMaximized();
    }
  }, [maximized, mobileDockMode, open, path, toggleMaximized]);

  const resizeTranscript = useCallback((nextHeight: number) => {
    const pane = paneRef.current;
    if (!pane) {
      return;
    }
    setHeightPx(clampTranscriptHeight(nextHeight, pane));
  }, []);

  const handleResizePointerDown = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      if (maximized) {
        return;
      }
      const pane = paneRef.current;
      if (!pane) {
        return;
      }

      event.preventDefault();
      if (!open) {
        toggleOpen();
      }
      const startY = event.clientY;
      const startHeight = pane.getBoundingClientRect().height;
      const originalCursor = document.body.style.cursor;
      const originalUserSelect = document.body.style.userSelect;
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";

      const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
        moveEvent.preventDefault();
        resizeTranscript(startHeight + startY - moveEvent.clientY);
      };

      const handlePointerUp = () => {
        document.body.style.cursor = originalCursor;
        document.body.style.userSelect = originalUserSelect;
        window.removeEventListener("pointermove", handlePointerMove);
        window.removeEventListener("pointerup", handlePointerUp);
      };

      window.addEventListener("pointermove", handlePointerMove);
      window.addEventListener("pointerup", handlePointerUp, { once: true });
    },
    [maximized, open, resizeTranscript, toggleOpen],
  );

  const handleResizeKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (maximized) {
        return;
      }
      const pane = paneRef.current;
      if (!pane) {
        return;
      }

      const currentHeight = heightPx ?? pane.getBoundingClientRect().height;
      const step = event.shiftKey ? RESIZE_LARGE_STEP_PX : RESIZE_STEP_PX;

      if (event.key === "ArrowUp") {
        event.preventDefault();
        if (!open) {
          toggleOpen();
        }
        resizeTranscript(currentHeight + step);
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        if (!open) {
          toggleOpen();
        }
        resizeTranscript(currentHeight - step);
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        if (!open) {
          toggleOpen();
        }
        resizeTranscript(MIN_TRANSCRIPT_HEIGHT_PX);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        if (!open) {
          toggleOpen();
        }
        resizeTranscript(getMaxTranscriptHeight(pane));
      }
    },
    [heightPx, maximized, open, resizeTranscript, toggleOpen],
  );

  if (!openFile) {
    return null;
  }

  const style =
    heightPx && open && !maximized
      ? ({ "--transcript-height": `${heightPx}px` } as CSSProperties)
      : undefined;

  return (
    <section
      ref={paneRef}
      className={`transcript-pane ${maximized ? "transcript-pane--maximized" : ""} ${
        open ? "transcript-pane--open" : "transcript-pane--closed"
      }`}
      style={style}
    >
      {!maximized ? (
        <div
          className="transcript-resize-handle"
          role="separator"
          aria-label="Resize transcript"
          aria-orientation="horizontal"
          tabIndex={0}
          onPointerDown={handleResizePointerDown}
          onKeyDown={handleResizeKeyDown}
        />
      ) : null}
      <TranscriptToolbar
        open={open}
        maximized={maximized}
        mobileDockMode={mobileDockMode}
        filterMode={filterMode}
        sortOrder={sortOrder}
        hasRows={rows.length > 0}
        onToggleOpen={mobileDockMode && !open ? toggleMaximized : toggleOpen}
        onToggleMaximized={toggleMaximized}
        onFilter={setFilter}
        onToggleSort={toggleSort}
      />
      {open ? (
        <>
          {status === "loading" ? <p className="muted transcript-empty">Loading transcript...</p> : null}
          {error ? <p className="error-text">{error}</p> : null}
          <div ref={scrollerRef} className="transcript-scroll" onScroll={handleScroll}>
            <TranscriptList
              rows={rows}
              filterMode={filterMode}
              sortOrder={sortOrder}
              expandedRows={expandedRows}
              onToggleExpand={toggleExpand}
              onDeleteRow={(rowNumber) => void deleteRow(client, rowNumber)}
              onDeleteSearchResult={(rowNumber, resultIndex) =>
                void deleteSearchResult(client, rowNumber, resultIndex)
              }
            />
          </div>
        </>
      ) : null}
    </section>
  );
}

function useMediaQuery(query: string): boolean {
  const getMatches = useCallback(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return false;
    }
    return window.matchMedia(query).matches;
  }, [query]);
  const [matches, setMatches] = useState(getMatches);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia(query);
    const handleChange = () => setMatches(media.matches);
    handleChange();
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [query]);

  return matches;
}

function clampTranscriptHeight(height: number, pane: HTMLElement): number {
  return Math.min(
    Math.max(height, MIN_TRANSCRIPT_HEIGHT_PX),
    getMaxTranscriptHeight(pane),
  );
}

function getMaxTranscriptHeight(pane: HTMLElement): number {
  const workspace = pane.closest(".workspace-main") as HTMLElement | null;
  const prompt = workspace?.querySelector(".prompt-composer") as HTMLElement | null;
  const workspaceHeight = workspace?.clientHeight ?? window.innerHeight;
  const promptHeight = prompt?.getBoundingClientRect().height ?? 0;
  return Math.max(
    MIN_TRANSCRIPT_HEIGHT_PX,
    workspaceHeight - promptHeight - EDITOR_RESERVE_PX,
  );
}
