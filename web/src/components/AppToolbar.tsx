import { useCallback } from "react";
import { useNoteEditorStore } from "../state/noteEditor";
import { useWs } from "../ws/context";
import { ConnectionBadge } from "./ConnectionBadge";

interface AppToolbarProps {
  explorerOpen: boolean;
  onToggleExplorer: () => void;
}

export function AppToolbar({ explorerOpen, onToggleExplorer }: AppToolbarProps) {
  const { client } = useWs();
  const path = useNoteEditorStore((store) => store.path);
  const currentDoc = useNoteEditorStore((store) => store.currentDoc);
  const dirty = useNoteEditorStore((store) => store.dirty);
  const status = useNoteEditorStore((store) => store.status);
  const save = useNoteEditorStore((store) => store.save);

  const saveCurrentDoc = useCallback(() => {
    void save(client, currentDoc);
  }, [client, currentDoc, save]);

  const saveDisabled = !path || status === "loading" || status === "saving" || !dirty;
  const saveLabel =
    status === "saving" ? "Saving file" : dirty ? "Save current file" : "File saved";
  const displayName = path ? basename(path) : "No file selected";

  return (
    <header className="app-toolbar">
      <div className="app-toolbar__group app-toolbar__group--left">
        <button
          type="button"
          className="toolbar-icon-button"
          aria-label={explorerOpen ? "Close file explorer" : "Open file explorer"}
          aria-pressed={explorerOpen}
          onClick={onToggleExplorer}
        >
          📁
        </button>
        <button
          type="button"
          className="toolbar-icon-button"
          aria-label="Settings"
          disabled
        >
          ⚙
        </button>
        <button
          type="button"
          className={`toolbar-save-button ${dirty ? "toolbar-save-button--dirty" : ""}`}
          aria-label={saveLabel}
          title={saveLabel}
          onClick={saveCurrentDoc}
          disabled={saveDisabled}
        >
          ⬇️
        </button>
      </div>

      <div className="app-toolbar__title" aria-label={`Active file ${displayName}`}>
        <span className="app-toolbar__filename">{displayName}</span>
        <ConnectionBadge />
      </div>

      <div className="app-toolbar__group app-toolbar__group--right">
        <button type="button" className="toolbar-menu-button" disabled>
          Included⌄
        </button>
        <button type="button" className="toolbar-menu-button" disabled>
          Plans⌄
        </button>
      </div>
    </header>
  );
}

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.at(-1) ?? path;
}
