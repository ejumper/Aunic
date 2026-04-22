import { useEffect, useRef, useState } from "react";
import { AppToolbar } from "./components/AppToolbar";
import { ConnectionDebug } from "./components/ConnectionDebug";
import { FileExplorer } from "./components/FileExplorer";
import { HelloPanel } from "./components/HelloPanel";
import { RawLog } from "./components/RawLog";
import { NoteEditor } from "./components/editor/NoteEditor";
import { PromptComposer } from "./components/prompt/PromptComposer";
import { TranscriptPane } from "./components/transcript/TranscriptPane";
import { useExplorerStore } from "./state/explorer";
import { useTranscriptStore } from "./state/transcript";
import { useConnectionState, WsProvider } from "./ws/context";
import { EditorView } from "@codemirror/view";
import { noteEditorRef } from "./noteEditorRef";
import { promptEditorRef } from "./promptEditorRef";

export function App() {
  const showDebug =
    import.meta.env.DEV && new URLSearchParams(window.location.search).has("debug");

  return (
    <WsProvider>
      <AppContent showDebug={showDebug} />
    </WsProvider>
  );
}

function AppContent({ showDebug }: { showDebug: boolean }) {
  const transcriptMaximized = useTranscriptStore((store) => store.maximized);
  const openFile = useExplorerStore((store) => store.open);
  const { state: connectionState } = useConnectionState();
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [noteKeyboardOpen, setNoteKeyboardOpen] = useState(false);
  const hasRestoredRef = useRef(false);

  function handleExplorerFileOpen() {
    if (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 760px)").matches
    ) {
      setExplorerOpen(false);
    }
  }

  useEffect(() => {
    if (connectionState !== "open" || hasRestoredRef.current) return;
    hasRestoredRef.current = true;
    const saved = localStorage.getItem("aunic:lastOpenFile");
    if (saved) {
      openFile(saved);
    }
  }, [connectionState, openFile]);

  useEffect(() => {
    const viewport = window.visualViewport;
    let largestViewportHeight = Math.max(viewport?.height ?? 0, window.innerHeight);

    function resetPageScroll() {
      window.scrollTo(0, 0);
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
    }

    function updateViewportState() {
      const visualHeight = viewport?.height ?? window.innerHeight;
      largestViewportHeight = Math.max(largestViewportHeight, visualHeight, window.innerHeight);
      const keyboardOpen = largestViewportHeight - visualHeight > 120;

      document.documentElement.style.setProperty(
        "--aunic-viewport-height",
        keyboardOpen ? `${visualHeight}px` : "100lvh",
      );
      document.documentElement.style.setProperty(
        "--aunic-viewport-top",
        `${keyboardOpen ? viewport?.offsetTop ?? 0 : 0}px`,
      );
      document.documentElement.style.setProperty(
        "--aunic-viewport-bottom",
        keyboardOpen && viewport
          ? `${Math.max(0, window.innerHeight - viewport.offsetTop - visualHeight)}px`
          : "0px",
      );

      const activeElement = document.activeElement;
      const noteFocused =
        activeElement instanceof HTMLElement &&
        Boolean(activeElement.closest(".code-editor-host"));
      setNoteKeyboardOpen(noteFocused && keyboardOpen);

      if (keyboardOpen || window.scrollY !== 0) {
        resetPageScroll();
        requestAnimationFrame(resetPageScroll);
      }
    }

    updateViewportState();
    window.addEventListener("resize", updateViewportState);
    window.addEventListener("focusin", updateViewportState);
    window.addEventListener("focusout", updateViewportState);
    viewport?.addEventListener("resize", updateViewportState);
    viewport?.addEventListener("scroll", updateViewportState);
    return () => {
      window.removeEventListener("resize", updateViewportState);
      window.removeEventListener("focusin", updateViewportState);
      window.removeEventListener("focusout", updateViewportState);
      viewport?.removeEventListener("resize", updateViewportState);
      viewport?.removeEventListener("scroll", updateViewportState);
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (!event.ctrlKey || event.key !== "e") {
        return;
      }
      event.preventDefault();
      const noteView = noteEditorRef.get();
      const promptView = promptEditorRef.get();
      if (!noteView || !promptView) {
        return;
      }
      const noteHasFocus = noteView.hasFocus;
      if (noteHasFocus) {
        promptView.focus();
      } else {
        noteView.focus();
        noteView.dispatch({
          effects: EditorView.scrollIntoView(noteView.state.selection.main.head, {
            y: "start",
            yMargin: noteView.defaultLineHeight * 3,
          }),
        });
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <main
      className={[
        "app-shell",
        noteKeyboardOpen ? "app-shell--note-keyboard-open" : "",
      ].filter(Boolean).join(" ")}
    >
      <AppToolbar
        explorerOpen={explorerOpen}
        onToggleExplorer={() => setExplorerOpen((current) => !current)}
      />
      <ConnectionDebug />
      <div
        className={`workspace-layout ${
          explorerOpen ? "" : "workspace-layout--explorer-closed"
        }`}
      >
        {explorerOpen ? <FileExplorer onOpenFile={handleExplorerFileOpen} /> : null}
        <div
          className={`workspace-main ${
            transcriptMaximized ? "workspace-main--transcript-maximized" : ""
          }`}
        >
          <div className="editor-region">
            <NoteEditor />
          </div>
          <div className="run-dock">
            <TranscriptPane />
            <PromptComposer />
          </div>
          {showDebug ? (
            <details className="debug-details">
              <summary>Debug</summary>
              <HelloPanel />
            </details>
          ) : null}
        </div>
      </div>
      {showDebug ? <RawLog /> : null}
    </main>
  );
}
