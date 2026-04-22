import { useEffect, useMemo, useRef, useState, type ComponentProps, type FormEvent } from "react";
import {
  Tree,
  TreeItem,
  TreeItemContent,
  type Key,
  type Selection,
} from "react-aria-components";
import { useExplorerStore, parentDir, ROOT_DIR } from "../state/explorer";
import { useNoteEditorStore } from "../state/noteEditor";
import { useSessionStore } from "../state/session";
import { useConnectionState, useWs } from "../ws/context";
import type { FileEntryPayload } from "../ws/types";

type CreateIntent = {
  kind: "file" | "dir";
  dirPath: string;
} | null;

type FileExplorerProps = {
  onOpenFile?: () => void;
};

type TreeItemPressEvent = Parameters<NonNullable<ComponentProps<typeof TreeItem>["onPress"]>>[0];

export function FileExplorer({ onOpenFile }: FileExplorerProps) {
  const { client } = useWs();
  const { state: connectionState } = useConnectionState();
  const session = useSessionStore((store) => store.session);
  const entriesByDir = useExplorerStore((store) => store.entriesByDir);
  const expanded = useExplorerStore((store) => store.expanded);
  const loading = useExplorerStore((store) => store.loading);
  const selected = useExplorerStore((store) => store.selected);
  const error = useExplorerStore((store) => store.error);
  const loadDir = useExplorerStore((store) => store.loadDir);
  const toggleExpand = useExplorerStore((store) => store.toggleExpand);
  const setExpanded = useExplorerStore((store) => store.setExpanded);
  const select = useExplorerStore((store) => store.select);
  const open = useExplorerStore((store) => store.open);
  const createFile = useExplorerStore((store) => store.createFile);
  const createDirectory = useExplorerStore((store) => store.createDirectory);
  const deleteEntry = useExplorerStore((store) => store.deleteEntry);
  const [createIntent, setCreateIntent] = useState<CreateIntent>(null);
  const [newName, setNewName] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [moreOpen, setMoreOpen] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const rootEntries = filterEntries(entriesByDir[ROOT_DIR] ?? [], showHidden);
  const entryMap = useMemo(() => buildEntryMap(entriesByDir), [entriesByDir]);
  const selectedEntry = selected ? entryMap.get(selected) : undefined;
  const targetDir =
    selectedEntry?.kind === "dir" ? selectedEntry.path : selected ? parentDir(selected) : ROOT_DIR;
  const rootLabel = workspaceLabel(session?.workspace_root);
  const isConnected = connectionState === "open";

  useEffect(() => {
    if (createIntent) {
      inputRef.current?.focus();
    }
  }, [createIntent]);

  function handleSelectionChange(selection: Selection) {
    if (selection === "all") {
      return;
    }
    const selectedKey = selection.values().next().value;
    if (selectedKey === undefined) {
      select(null);
      return;
    }

    const path = String(selectedKey);
    if (entryMap.get(path)?.kind === "file") {
      void openFile(path);
      return;
    }
    select(path);
  }

  function handleExpandedChange(keys: Set<Key>) {
    const nextExpanded = new Set([...keys].map((key) => String(key)));
    const newlyExpanded = [...nextExpanded].filter((path) => !expanded.has(path));
    setExpanded(nextExpanded);
    for (const path of newlyExpanded) {
      void loadDir(client, path);
    }
  }

  async function handleEntryAction(entry: FileEntryPayload) {
    setActionError(null);
    if (entry.kind === "file") {
      await openFile(entry.path);
      return;
    }
    await toggleExpand(client, entry.path);
  }

  async function flushAutosaveBeforeNavigation(message: string): Promise<boolean> {
    const noteState = useNoteEditorStore.getState();
    const currentSession = useSessionStore.getState();
    if (
      currentSession.session?.editor_settings?.save_mode !== "auto" ||
      !noteState.path ||
      !noteState.dirty
    ) {
      return true;
    }
    if (currentSession.runActive) {
      setActionError("Wait for the current run to finish before switching files. Autosave is paused during runs.");
      return false;
    }
    const saved = await noteState.save(client, noteState.currentDoc);
    if (!saved) {
      setActionError(message);
      return false;
    }
    return true;
  }

  async function openFile(path: string) {
    if (!(await flushAutosaveBeforeNavigation("Autosave failed before switching files."))) {
      return;
    }
    open(path);
    onOpenFile?.();
  }

  function startCreate(kind: "file" | "dir") {
    setActionError(null);
    setCreateIntent({ kind, dirPath: targetDir });
    setNewName("");
  }

  async function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!createIntent) {
      return;
    }

    setActionError(null);
    try {
      if (createIntent.kind === "file") {
        if (!(await flushAutosaveBeforeNavigation("Autosave failed before creating the new file."))) {
          return;
        }
        await createFile(client, createIntent.dirPath, newName);
      } else {
        await createDirectory(client, createIntent.dirPath, newName);
      }
      setCreateIntent(null);
      setNewName("");
    } catch (error) {
      setActionError(formatActionError(error));
    }
  }

  async function handleDelete() {
    if (!selected) {
      return;
    }
    const label = selectedEntry?.name ?? selected;
    if (!window.confirm(`Delete "${label}"? This cannot be undone.`)) {
      return;
    }
    setActionError(null);
    try {
      await deleteEntry(client, selected);
    } catch (error) {
      setActionError(formatActionError(error));
    }
  }

  async function handleRefresh() {
    setActionError(null);
    await loadDir(client, targetDir);
  }

  return (
    <aside className="explorer-panel" aria-label="File explorer">
      <div className="explorer-header">
        <p className="eyebrow">Workspace: <span>{rootLabel}</span></p>
        <button
          type="button"
          className="settings-button"
          aria-label="Settings"
          disabled
        >
          ⚙
        </button>
      </div>

      <div className="explorer-scrollable">
        {createIntent ? (
        <form className="explorer-create-form" onSubmit={handleCreateSubmit}>
          <label>
            {createIntent.kind === "file" ? "New markdown file" : "New folder"} in{" "}
            {createIntent.dirPath || "workspace root"}
            <input
              ref={inputRef}
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setCreateIntent(null);
                  setNewName("");
                }
              }}
              placeholder={createIntent.kind === "file" ? "scratch.md" : "scratch-dir"}
            />
          </label>
          <div className="explorer-create-actions">
            <button type="submit">Create</button>
            <button
              type="button"
              className="secondary-button"
              onClick={() => {
                setCreateIntent(null);
                setNewName("");
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}

      {actionError ? <p className="error-text">{actionError}</p> : null}
      {error[targetDir] ? <p className="error-text">{error[targetDir]}</p> : null}

      <Tree
        aria-label={`${rootLabel} files`}
        className="file-tree"
        selectionMode="single"
        selectionBehavior="replace"
        selectedKeys={selected ? new Set([selected]) : new Set()}
        expandedKeys={expanded}
        onSelectionChange={handleSelectionChange}
        onExpandedChange={handleExpandedChange}
        renderEmptyState={() => (
          <div className="tree-empty">
            {loading.has(ROOT_DIR) ? "Loading workspace..." : "No files in this folder."}
          </div>
        )}
      >
          {rootEntries.map((entry) => renderTreeItem(entry, entriesByDir, expanded, loading, handleEntryAction, showHidden))}
        </Tree>
      </div>

      {moreOpen ? (
        <div className="explorer-more-panel">
          <button
            type="button"
            className="explorer-more-item"
            onClick={handleRefresh}
            disabled={!isConnected}
          >
            Refresh
          </button>
          <label className="explorer-more-item explorer-more-toggle">
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => setShowHidden(e.target.checked)}
            />
            Show hidden files
          </label>
        </div>
      ) : null}

      <div className="explorer-toolbar" aria-label="File actions">
        <button type="button" onClick={() => startCreate("file")} disabled={!isConnected}>
          📄
        </button>
        <button type="button" onClick={() => startCreate("dir")} disabled={!isConnected}>
          📂
        </button>
        <button type="button" onClick={handleDelete} disabled={!isConnected || !selected}>
          ✕
        </button>
        <button
          type="button"
          className={moreOpen ? "explorer-more-btn explorer-more-btn--open" : "explorer-more-btn"}
          aria-expanded={moreOpen}
          onClick={() => setMoreOpen((v) => !v)}
        >
          {moreOpen ? "∨" : "∧"}
        </button>
      </div>
    </aside>
  );
}

function renderTreeItem(
  entry: FileEntryPayload,
  entriesByDir: Record<string, FileEntryPayload[]>,
  expanded: Set<string>,
  loading: Set<string>,
  onAction: (entry: FileEntryPayload) => void | Promise<void>,
  showHidden: boolean,
) {
  const isDir = entry.kind === "dir";
  const children = isDir && expanded.has(entry.path)
    ? filterEntries(entriesByDir[entry.path] ?? [], showHidden)
    : [];

  return (
    <TreeItem
      key={entry.path}
      id={entry.path}
      textValue={entry.name}
      hasChildItems={isDir}
      onAction={() => {
        void onAction(entry);
      }}
      {...(!isDir
        ? {
            onPress: (event: TreeItemPressEvent) => {
              if (event.pointerType === "touch") {
                void onAction(entry);
              }
            },
          }
        : {})}
    >
      <TreeItemContent>
        {({ isExpanded }) => (
          <span className="tree-row-label">
            <span className="tree-disclosure" aria-hidden="true">
              {isDir ? (isExpanded ? "▾" : "▸") : ""}
            </span>
            <span aria-hidden="true">{isDir ? "📁" : "📄"}</span>
            <span className="tree-entry-name">{entry.name}</span>
            {loading.has(entry.path) ? <span className="tree-loading">loading</span> : null}
          </span>
        )}
      </TreeItemContent>
      {children.map((child) => renderTreeItem(child, entriesByDir, expanded, loading, onAction, showHidden))}
    </TreeItem>
  );
}

function filterEntries(entries: FileEntryPayload[], showHidden: boolean): FileEntryPayload[] {
  return showHidden ? entries : entries.filter((e) => !e.name.startsWith("."));
}

function buildEntryMap(
  entriesByDir: Record<string, FileEntryPayload[]>,
): Map<string, FileEntryPayload> {
  const map = new Map<string, FileEntryPayload>();
  for (const entries of Object.values(entriesByDir)) {
    for (const entry of entries) {
      map.set(entry.path, entry);
    }
  }
  return map;
}

function workspaceLabel(workspaceRoot: string | undefined): string {
  if (!workspaceRoot) {
    return "Workspace";
  }
  const parts = workspaceRoot.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) ?? "Workspace";
}

function formatActionError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
