import { useEffect, useRef, useState } from "react";
import { AppToolbar } from "./components/AppToolbar";
import { FileExplorer } from "./components/FileExplorer";
import { HelloPanel } from "./components/HelloPanel";
import { RawLog } from "./components/RawLog";
import { NoteEditor } from "./components/editor/NoteEditor";
import { PromptComposer } from "./components/prompt/PromptComposer";
import { TranscriptPane } from "./components/transcript/TranscriptPane";
import { useExplorerStore } from "./state/explorer";
import { useTranscriptStore } from "./state/transcript";
import { useConnectionState, WsProvider } from "./ws/context";

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
  const hasRestoredRef = useRef(false);

  useEffect(() => {
    if (connectionState !== "open" || hasRestoredRef.current) return;
    hasRestoredRef.current = true;
    const saved = localStorage.getItem("aunic:lastOpenFile");
    if (saved) {
      openFile(saved);
    }
  }, [connectionState, openFile]);

  return (
    <main className="app-shell">
      <AppToolbar
        explorerOpen={explorerOpen}
        onToggleExplorer={() => setExplorerOpen((current) => !current)}
      />
      <div
        className={`workspace-layout ${
          explorerOpen ? "" : "workspace-layout--explorer-closed"
        }`}
      >
        {explorerOpen ? <FileExplorer /> : null}
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
