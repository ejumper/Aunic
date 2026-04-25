import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type FormEvent,
  type MouseEvent,
} from "react";
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
import type { FileEntryPayload, ProjectNodePayload, ProjectPlanPayload } from "../ws/types";

type CreateIntent = {
  kind: "file" | "dir";
  dirPath: string;
} | null;

type ExplorerContextMenuState = {
  path: string;
  name: string;
  kind: "file" | "dir";
  x: number;
  y: number;
} | null;

type RenameTarget = {
  path: string;
  name: string;
  kind: "file" | "dir";
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
  const openFile = useExplorerStore((store) => store.openFile);
  const sourceFile = useExplorerStore((store) => store.sourceFile);
  const error = useExplorerStore((store) => store.error);
  const viewMode = useExplorerStore((store) => store.viewMode);
  const projectState = useExplorerStore((store) => store.projectState);
  const projectExpanded = useExplorerStore((store) => store.projectExpanded);
  const projectSelected = useExplorerStore((store) => store.projectSelected);
  const projectLoading = useExplorerStore((store) => store.projectLoading);
  const projectError = useExplorerStore((store) => store.projectError);
  const loadDir = useExplorerStore((store) => store.loadDir);
  const toggleExpand = useExplorerStore((store) => store.toggleExpand);
  const setExpanded = useExplorerStore((store) => store.setExpanded);
  const select = useExplorerStore((store) => store.select);
  const open = useExplorerStore((store) => store.open);
  const openProjectFile = useExplorerStore((store) => store.openProjectFile);
  const returnToSource = useExplorerStore((store) => store.returnToSource);
  const setViewMode = useExplorerStore((store) => store.setViewMode);
  const loadProjectState = useExplorerStore((store) => store.loadProjectState);
  const refreshProjectState = useExplorerStore((store) => store.refreshProjectState);
  const toggleProjectExpand = useExplorerStore((store) => store.toggleProjectExpand);
  const selectProject = useExplorerStore((store) => store.selectProject);
  const addInclude = useExplorerStore((store) => store.addInclude);
  const removeIncludeEntry = useExplorerStore((store) => store.removeIncludeEntry);
  const deletePlan = useExplorerStore((store) => store.deletePlan);
  const setActivePlan = useExplorerStore((store) => store.setActivePlan);
  const setIncludeEntryActive = useExplorerStore((store) => store.setIncludeEntryActive);
  const setProjectChildActive = useExplorerStore((store) => store.setProjectChildActive);
  const createFile = useExplorerStore((store) => store.createFile);
  const createDirectory = useExplorerStore((store) => store.createDirectory);
  const createProjectFile = useExplorerStore((store) => store.createProjectFile);
  const createProjectDirectory = useExplorerStore((store) => store.createProjectDirectory);
  const renameEntry = useExplorerStore((store) => store.renameEntry);
  const deleteEntry = useExplorerStore((store) => store.deleteEntry);
  const [createIntent, setCreateIntent] = useState<CreateIntent>(null);
  const [newName, setNewName] = useState("");
  const [renameTarget, setRenameTarget] = useState<RenameTarget>(null);
  const [renameName, setRenameName] = useState("");
  const [moreOpen, setMoreOpen] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [showOnlyMarkdown, setShowOnlyMarkdown] = useState(true);
  const [contextMenu, setContextMenu] = useState<ExplorerContextMenuState>(null);
  const setIndicatorMessage = useSessionStore((store) => store.setIndicatorMessage);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const renameInputRef = useRef<HTMLInputElement | null>(null);
  const rootEntries = filterEntries(entriesByDir[ROOT_DIR] ?? [], {
    showHidden,
    showOnlyMarkdown,
  });
  const entryMap = useMemo(() => buildEntryMap(entriesByDir), [entriesByDir]);
  const selectedEntry = selected ? entryMap.get(selected) : undefined;
  const selectedProjectNode = useMemo(
    () => (projectSelected && projectState ? findProjectNodeById(projectState.entries, projectSelected) : null),
    [projectSelected, projectState],
  );
  const selectedProjectPlan = useMemo(
    () => (projectSelected && projectState ? findProjectPlanByRowId(projectState.plans, projectSelected) : null),
    [projectSelected, projectState],
  );
  const targetDir =
    selectedEntry?.kind === "dir" ? selectedEntry.path : selected ? parentDir(selected) : ROOT_DIR;
  const projectCreateDir = sourceFile ? parentDir(sourceFile) : ROOT_DIR;
  const rootLabel = workspaceLabel(session?.workspace_root);
  const isConnected = connectionState === "open";
  const inProjectMode = viewMode === "project";
  const modeToggleIcon = inProjectMode ? "🗃️" : "📑";
  const modeToggleLabel = inProjectMode ? "Show file tree" : "Show project files";
  const canDeleteProjectItem = Boolean(selectedProjectPlan || selectedProjectNode?.removable);

  useEffect(() => {
    if (createIntent) {
      inputRef.current?.focus();
    }
  }, [createIntent]);

  useEffect(() => {
    if (renameTarget) {
      renameInputRef.current?.focus();
      renameInputRef.current?.select();
    }
  }, [renameTarget]);

  useEffect(() => {
    if (error[targetDir]) {
      setIndicatorMessage(error[targetDir], "error");
    }
  }, [error, setIndicatorMessage, targetDir]);

  useEffect(() => {
    if (!projectError) {
      return;
    }
    setIndicatorMessage(projectError, "error");
  }, [projectError, setIndicatorMessage]);

  useEffect(() => {
    if (connectionState !== "open" || !sourceFile) {
      return;
    }
    void loadProjectState(client, sourceFile);
  }, [client, connectionState, loadProjectState, sourceFile]);

  useEffect(() => {
    if (!contextMenu) {
      return;
    }
    function close() {
      setContextMenu(null);
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        close();
      }
    }
    window.addEventListener("pointerdown", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [contextMenu]);

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
      void openTreeFile(path);
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
    if (entry.kind === "file") {
      await openTreeFile(entry.path);
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
      setIndicatorMessage(
        "Wait for the current run to finish before switching files. Autosave is paused during runs.",
        "error",
      );
      return false;
    }
    const saved = await noteState.save(client, noteState.currentDoc);
    if (!saved) {
      setIndicatorMessage(message, "error");
      return false;
    }
    return true;
  }

  async function openTreeFile(path: string) {
    if (!(await flushAutosaveBeforeNavigation("Autosave failed before switching files."))) {
      return;
    }
    open(path);
    setIndicatorMessage(`Opened ${pathLabel(path)}.`);
    onOpenFile?.();
  }

  async function openIncludedFile(node: ProjectNodePayload) {
    if (!node.openable || !node.effective_active) {
      return;
    }
    if (!(await flushAutosaveBeforeNavigation("Autosave failed before switching project files."))) {
      return;
    }
    openProjectFile(node.path, node.id);
    setIndicatorMessage(`Opened ${pathLabel(node.path)}.`);
    onOpenFile?.();
  }

  async function openPlanFile(plan: ProjectPlanPayload) {
    if (!plan.openable) {
      return;
    }
    if (!(await flushAutosaveBeforeNavigation("Autosave failed before switching plans."))) {
      return;
    }
    try {
      await setActivePlan(client, plan.plan_id);
      openProjectFile(plan.path, plan.id);
      setIndicatorMessage(`Opened ${pathLabel(plan.path)}.`);
      onOpenFile?.();
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  async function handleReturnToSource() {
    if (!sourceFile) {
      return;
    }
    if (!(await flushAutosaveBeforeNavigation("Autosave failed before returning to the source file."))) {
      return;
    }
    returnToSource();
    setIndicatorMessage(`Returned to ${pathLabel(sourceFile)}.`);
    onOpenFile?.();
  }

  function startCreate(kind: "file" | "dir", dirPathOverride?: string) {
    setCreateIntent({
      kind,
      dirPath: dirPathOverride ?? (inProjectMode ? projectCreateDir : targetDir),
    });
    setNewName("");
  }

  async function handleCreateSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!createIntent) {
      return;
    }

    try {
      if (inProjectMode) {
        if (createIntent.kind === "file") {
          const createdPath = await createProjectFile(client, newName);
          setIndicatorMessage(`Created ${newName.trim()} and added it to project files.`);
          onOpenFile?.();
          selectProject(
            findProjectNodeByPath(
              useExplorerStore.getState().projectState?.entries ?? [],
              createdPath,
              "entry",
            )?.id ?? null,
          );
        } else {
          await createProjectDirectory(client, newName);
          setIndicatorMessage(`Created ${newName.trim()} and added it to project files.`);
        }
      } else if (createIntent.kind === "file") {
        if (!(await flushAutosaveBeforeNavigation("Autosave failed before creating the new file."))) {
          return;
        }
        await createFile(client, createIntent.dirPath, newName);
        setIndicatorMessage(`Created ${newName.trim()}.`);
        onOpenFile?.();
      } else {
        await createDirectory(client, createIntent.dirPath, newName);
        setIndicatorMessage(`Created ${newName.trim()}.`);
      }
      setCreateIntent(null);
      setNewName("");
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  async function handleDelete() {
    if (inProjectMode) {
      if (selectedProjectPlan) {
        const label = selectedProjectPlan.name || selectedProjectPlan.title;
        if (!window.confirm(`Delete plan "${label}"? This cannot be undone.`)) {
          return;
        }
        try {
          await deletePlan(client, selectedProjectPlan.plan_id);
          setIndicatorMessage(`Deleted plan ${label}.`);
          if (openFile === selectedProjectPlan.path) {
            onOpenFile?.();
          }
        } catch (error) {
          setIndicatorMessage(formatActionError(error), "error");
        }
        return;
      }
      if (!selectedProjectNode || !selectedProjectNode.removable) {
        return;
      }
      const label = selectedProjectNode.name || selectedProjectNode.path;
      if (!window.confirm(`Remove "${label}" from project files?`)) {
        return;
      }
      try {
        if (selectedProjectNode.scope === "entry") {
          await removeIncludeEntry(client, selectedProjectNode.path);
        } else {
          await setProjectChildActive(client, selectedProjectNode.path, false);
        }
        setIndicatorMessage(`Removed ${label} from project files.`);
      } catch (error) {
        setIndicatorMessage(formatActionError(error), "error");
      }
      return;
    }

    if (!selected) {
      return;
    }
    const label = selectedEntry?.name ?? selected;
    if (!window.confirm(`Delete "${label}"? This cannot be undone.`)) {
      return;
    }
    try {
      await deleteEntry(client, selected);
      setIndicatorMessage(`Deleted ${label}.`);
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  async function handleRefresh() {
    if (inProjectMode) {
      await refreshProjectState(client);
      return;
    }
    await loadDir(client, targetDir);
  }

  async function handleRenameSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!renameTarget) {
      return;
    }
    try {
      await renameEntry(client, renameTarget.path, renameName);
      setIndicatorMessage(`Renamed to ${renameName.trim()}.`);
      setRenameTarget(null);
      setRenameName("");
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  async function handleIncludeContextAction() {
    if (!contextMenu) {
      return;
    }
    if (!sourceFile) {
      setIndicatorMessage("Open a source file before including project files.", "error");
      setContextMenu(null);
      return;
    }
    try {
      await addInclude(client, contextMenu.path, { recursive: contextMenu.kind === "dir" });
      setIndicatorMessage(`Included ${contextMenu.kind === "dir" ? "folder" : "file"}: ${contextMenu.name}`);
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    } finally {
      setContextMenu(null);
    }
  }

  function handleCopyNameContextAction() {
    if (!contextMenu) {
      return;
    }
    void navigator.clipboard.writeText(contextMenu.name);
    setIndicatorMessage(`Copied name: ${contextMenu.name}`);
    setContextMenu(null);
  }

  function handleCopyPathContextAction() {
    if (!contextMenu) {
      return;
    }
    const absPath = session?.workspace_root
      ? `${session.workspace_root}/${contextMenu.path}`
      : contextMenu.path;
    void navigator.clipboard.writeText(absPath);
    setIndicatorMessage(`Copied path.`);
    setContextMenu(null);
  }

  function handleRenameContextAction() {
    if (!contextMenu) {
      return;
    }
    setRenameTarget({ path: contextMenu.path, name: contextMenu.name, kind: contextMenu.kind });
    setRenameName(contextMenu.name);
    setCreateIntent(null);
    setContextMenu(null);
  }

  async function handleDeleteContextAction() {
    if (!contextMenu) {
      return;
    }
    const { path, name } = contextMenu;
    setContextMenu(null);
    if (!window.confirm(`Delete "${name}"? This cannot be undone.`)) {
      return;
    }
    try {
      await deleteEntry(client, path);
      setIndicatorMessage(`Deleted ${name}.`);
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  function handleTreeContextMenu(event: MouseEvent<HTMLElement>, entry: FileEntryPayload) {
    event.preventDefault();
    setContextMenu({
      path: entry.path,
      name: entry.name,
      kind: entry.kind,
      x: event.clientX,
      y: event.clientY,
    });
  }

  function handleProjectNodeClick(node: ProjectNodePayload) {
    if (node.kind === "dir") {
      selectProject(node.id);
      toggleProjectExpand(node.id);
      return;
    }
    selectProject(node.id);
    void openIncludedFile(node);
  }

  async function handleProjectNodeToggle(node: ProjectNodePayload, checked: boolean) {
    try {
      if (node.scope === "entry") {
        await setIncludeEntryActive(client, node.path, checked);
      } else {
        await setProjectChildActive(client, node.path, checked);
      }
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  async function handleProjectPlanToggle(plan: ProjectPlanPayload, checked: boolean) {
    try {
      await setActivePlan(client, checked ? plan.plan_id : null);
    } catch (error) {
      setIndicatorMessage(formatActionError(error), "error");
    }
  }

  return (
    <aside className="explorer-panel" aria-label="File explorer">
      <div className="explorer-header">
        <p className="eyebrow">Workspace: <span>{rootLabel}</span></p>
        <button
          type="button"
          className={`settings-button explorer-mode-toggle ${
            inProjectMode ? "explorer-mode-toggle--project" : ""
          }`}
          aria-label={modeToggleLabel}
          title={modeToggleLabel}
          onClick={() => setViewMode(inProjectMode ? "tree" : "project")}
        >
          {modeToggleIcon}
        </button>
      </div>

      <div className="explorer-scrollable">
        {renameTarget ? (
          <form className="explorer-create-form" onSubmit={handleRenameSubmit}>
            <label>
              Rename "{renameTarget.name}"
              <input
                ref={renameInputRef}
                value={renameName}
                onChange={(event) => setRenameName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    setRenameTarget(null);
                    setRenameName("");
                  }
                }}
              />
            </label>
            <div className="explorer-create-actions">
              <button type="submit">Rename</button>
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  setRenameTarget(null);
                  setRenameName("");
                }}
              >
                Cancel
              </button>
            </div>
          </form>
        ) : createIntent ? (
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

        {inProjectMode ? (
          <div className="project-tree" aria-label="Project files">
            {sourceFile && openFile && sourceFile !== openFile ? (
              <button
                type="button"
                className="project-return-row"
                onClick={() => void handleReturnToSource()}
              >
                {`← return to ${pathLabel(sourceFile)}`}
              </button>
            ) : null}

            {!sourceFile ? (
              <div className="tree-empty">Open a markdown file from the file tree to manage project files.</div>
            ) : projectLoading && !projectState ? (
              <div className="tree-empty">Loading project files...</div>
            ) : projectState && (projectState.entries.length > 0 || projectState.plans.length > 0) ? (
              <div className="project-tree__sections">
                {projectState.entries.length > 0 ? (
                  <section className="project-tree__section" aria-label="Included files">
                    <p className="project-tree__section-label">Included</p>
                    <div className="project-tree__nodes">
                      {projectState.entries.map((node) => (
                        <ProjectTreeNode
                          key={node.id}
                          node={node}
                          depth={0}
                          expanded={projectExpanded}
                          selectedId={projectSelected}
                          onSelect={selectProject}
                          onToggleExpand={toggleProjectExpand}
                          onClick={handleProjectNodeClick}
                          onToggleChecked={handleProjectNodeToggle}
                        />
                      ))}
                    </div>
                  </section>
                ) : null}
                {projectState.plans.length > 0 ? (
                  <section className="project-tree__section" aria-label="Plans">
                    <p className="project-tree__section-label">Plans</p>
                    <div className="project-tree__nodes">
                      {projectState.plans.map((plan) => (
                        <ProjectPlanRow
                          key={plan.id}
                          plan={plan}
                          selectedId={projectSelected}
                          onSelect={selectProject}
                          onClick={openPlanFile}
                          onToggleChecked={handleProjectPlanToggle}
                        />
                      ))}
                    </div>
                  </section>
                ) : null}
              </div>
            ) : (
              <div className="tree-empty">
                No project files or plans yet. Use <code>/include</code> or <code>/plan</code> to add them.
              </div>
            )}
          </div>
        ) : (
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
            {rootEntries.map((entry) =>
              renderTreeItem(
                entry,
                entriesByDir,
                expanded,
                loading,
                handleEntryAction,
                showHidden,
                showOnlyMarkdown,
                handleTreeContextMenu,
              ),
            )}
          </Tree>
        )}
      </div>

      {moreOpen ? (
        <div className="explorer-more-panel">
          <button
            type="button"
            className="explorer-more-item"
            onClick={() => void handleRefresh()}
            disabled={!isConnected}
          >
            Refresh
          </button>
          {!inProjectMode ? (
            <label className="explorer-more-item explorer-more-toggle">
              <input
                type="checkbox"
                checked={showHidden}
                onChange={(e) => setShowHidden(e.target.checked)}
              />
              Show hidden files
            </label>
          ) : null}
          {!inProjectMode ? (
            <label className="explorer-more-item explorer-more-toggle">
              <input
                type="checkbox"
                checked={showOnlyMarkdown}
                onChange={(e) => setShowOnlyMarkdown(e.target.checked)}
              />
              Show only md files
            </label>
          ) : null}
        </div>
      ) : null}

      <div className="explorer-toolbar" aria-label="File actions">
        <button
          type="button"
          onClick={() => startCreate("file")}
          disabled={!isConnected || (inProjectMode && !sourceFile)}
        >
          📄
        </button>
        <button
          type="button"
          onClick={() => startCreate("dir")}
          disabled={!isConnected || (inProjectMode && !sourceFile)}
        >
          📂
        </button>
        <button
          type="button"
          onClick={() => void handleDelete()}
          disabled={!isConnected || (inProjectMode ? !canDeleteProjectItem : !selected)}
        >
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

      {contextMenu ? (
        <div
          className="explorer-context-menu"
          role="menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onPointerDown={(event) => event.stopPropagation()}
        >
          <button type="button" className="explorer-context-menu__item" onClick={() => void handleIncludeContextAction()}>
            Include
          </button>
          {contextMenu.kind === "dir" ? (
            <>
              <hr className="explorer-context-menu__separator" />
              <button
                type="button"
                className="explorer-context-menu__item"
                onClick={() => { startCreate("file", contextMenu.path); setContextMenu(null); }}
              >
                New Note
              </button>
              <button
                type="button"
                className="explorer-context-menu__item"
                onClick={() => { startCreate("dir", contextMenu.path); setContextMenu(null); }}
              >
                New Folder
              </button>
            </>
          ) : null}
          <hr className="explorer-context-menu__separator" />
          <button type="button" className="explorer-context-menu__item" onClick={handleCopyNameContextAction}>
            Copy Name
          </button>
          <button type="button" className="explorer-context-menu__item" onClick={handleCopyPathContextAction}>
            Copy Path
          </button>
          <hr className="explorer-context-menu__separator" />
          <button type="button" className="explorer-context-menu__item" onClick={handleRenameContextAction}>
            Rename
          </button>
          <button
            type="button"
            className="explorer-context-menu__item explorer-context-menu__item--danger"
            onClick={() => void handleDeleteContextAction()}
          >
            Delete
          </button>
        </div>
      ) : null}
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
  showOnlyMarkdown: boolean,
  onContextMenu: (event: MouseEvent<HTMLElement>, entry: FileEntryPayload) => void,
) {
  const isDir = entry.kind === "dir";
  const children = isDir && expanded.has(entry.path)
    ? filterEntries(entriesByDir[entry.path] ?? [], {
        showHidden,
        showOnlyMarkdown,
      })
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
          <span
            className="tree-row-label"
            onContextMenu={(event) => onContextMenu(event, entry)}
          >
            <span className="tree-disclosure" aria-hidden="true">
              {isDir ? (isExpanded ? "▾" : "▸") : ""}
            </span>
            <span aria-hidden="true">{isDir ? "📁" : "📄"}</span>
            <span className="tree-entry-name">{entry.name}</span>
            {loading.has(entry.path) ? <span className="tree-loading">loading</span> : null}
          </span>
        )}
      </TreeItemContent>
      {children.map((child) =>
        renderTreeItem(
          child,
          entriesByDir,
          expanded,
          loading,
          onAction,
          showHidden,
          showOnlyMarkdown,
          onContextMenu,
        ),
      )}
    </TreeItem>
  );
}

function ProjectTreeNode({
  node,
  depth,
  expanded,
  selectedId,
  onSelect,
  onToggleExpand,
  onClick,
  onToggleChecked,
}: {
  node: ProjectNodePayload;
  depth: number;
  expanded: Set<string>;
  selectedId: string | null;
  onSelect: (nodeId: string | null) => void;
  onToggleExpand: (nodeId: string) => void;
  onClick: (node: ProjectNodePayload) => void;
  onToggleChecked: (node: ProjectNodePayload, checked: boolean) => Promise<void>;
}) {
  const isExpanded = expanded.has(node.id);
  const disabled = node.scope === "child" && !node.effective_active;
  const selectable = node.kind === "file" ? node.openable && !disabled : true;

  return (
    <div className={`project-tree-node ${selectedId === node.id ? "project-tree-node--selected" : ""}`}>
      <button
        type="button"
        className={`project-tree-row ${disabled ? "project-tree-row--disabled" : ""}`}
        style={{ paddingLeft: `${depth * 18 + 8}px` }}
        onClick={() => {
          onSelect(node.id);
          if (node.kind === "dir") {
            onToggleExpand(node.id);
            return;
          }
          if (selectable) {
            onClick(node);
          }
        }}
      >
        <span className="project-tree-row__disclosure" aria-hidden="true">
          {node.kind === "dir" ? (isExpanded ? "▾" : "▸") : ""}
        </span>
        {node.checkable ? (
          <input
            type="checkbox"
            checked={node.active}
            disabled={disabled}
            onChange={(event) => {
              event.stopPropagation();
              void onToggleChecked(node, event.currentTarget.checked);
            }}
            onClick={(event) => event.stopPropagation()}
          />
        ) : (
          <span className="project-tree-row__checkbox-spacer" aria-hidden="true" />
        )}
        <span className="project-tree-row__icon" aria-hidden="true">
          {node.kind === "dir" ? "📁" : "📄"}
        </span>
        <span className="project-tree-row__name">{node.name}</span>
        {!node.exists ? <span className="project-tree-row__meta">missing</span> : null}
        {node.scope === "entry" && node.kind === "dir" && node.recursive ? (
          <span className="project-tree-row__meta">recursive</span>
        ) : null}
      </button>
      {node.kind === "dir" && isExpanded
        ? node.children.map((child) => (
            <ProjectTreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              selectedId={selectedId}
              onSelect={onSelect}
              onToggleExpand={onToggleExpand}
              onClick={onClick}
              onToggleChecked={onToggleChecked}
            />
          ))
        : null}
    </div>
  );
}

function ProjectPlanRow({
  plan,
  selectedId,
  onSelect,
  onClick,
  onToggleChecked,
}: {
  plan: ProjectPlanPayload;
  selectedId: string | null;
  onSelect: (nodeId: string | null) => void;
  onClick: (plan: ProjectPlanPayload) => void | Promise<void>;
  onToggleChecked: (plan: ProjectPlanPayload, checked: boolean) => Promise<void>;
}) {
  return (
    <div className={`project-tree-node ${selectedId === plan.id ? "project-tree-node--selected" : ""}`}>
      <button
        type="button"
        className="project-tree-row project-tree-row--plan"
        onClick={() => {
          onSelect(plan.id);
          if (plan.openable) {
            void onClick(plan);
          }
        }}
      >
        <span className="project-tree-row__disclosure" aria-hidden="true" />
        <input
          type="checkbox"
          checked={plan.active}
          onChange={(event) => {
            event.stopPropagation();
            void onToggleChecked(plan, event.currentTarget.checked);
          }}
          onClick={(event) => event.stopPropagation()}
        />
        <span className="project-tree-row__icon" aria-hidden="true">
          📄
        </span>
        <span className="project-tree-row__name">{plan.name}</span>
        <span className="project-tree-row__meta">{plan.status}</span>
        {!plan.exists ? <span className="project-tree-row__meta">missing</span> : null}
      </button>
    </div>
  );
}

export function filterEntries(
  entries: FileEntryPayload[],
  {
    showHidden,
    showOnlyMarkdown,
  }: {
    showHidden: boolean;
    showOnlyMarkdown: boolean;
  },
): FileEntryPayload[] {
  return entries.filter((entry) => {
    if (!showHidden && entry.name.startsWith(".")) {
      return false;
    }
    if (!showOnlyMarkdown || entry.kind === "dir") {
      return true;
    }
    return entry.name.toLowerCase().endsWith(".md");
  });
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

function findProjectNodeById(nodes: ProjectNodePayload[], nodeId: string): ProjectNodePayload | null {
  const stack = [...nodes];
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node) {
      continue;
    }
    if (node.id === nodeId) {
      return node;
    }
    stack.push(...node.children);
  }
  return null;
}

function findProjectNodeByPath(
  nodes: ProjectNodePayload[],
  path: string,
  scope?: ProjectNodePayload["scope"],
): ProjectNodePayload | null {
  const stack = [...nodes];
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node) {
      continue;
    }
    if (node.path === path && (scope === undefined || node.scope === scope)) {
      return node;
    }
    stack.push(...node.children);
  }
  return null;
}

function findProjectPlanByRowId(
  plans: ProjectPlanPayload[],
  rowId: string,
): ProjectPlanPayload | null {
  return plans.find((plan) => plan.id === rowId) ?? null;
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

function pathLabel(path: string): string {
  const normalized = path.replace(/\/+$/, "");
  const index = normalized.lastIndexOf("/");
  return index === -1 ? normalized : normalized.slice(index + 1);
}
