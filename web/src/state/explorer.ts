import { create } from "zustand";
import { getOrCreateBrowserInstanceId, getLastOpenFileStorageKey } from "../browserSession";
import type { WsClient } from "../ws/client";
import type {
  FileChangedPayload,
  FileEntryPayload,
  ProjectNodePayload,
  ProjectPlanPayload,
  ProjectStatePayload,
} from "../ws/types";

export const ROOT_DIR = "";
const MAX_ENTRY_NAME_BYTES = 255;

export type ExplorerViewMode = "tree" | "project";
export type ExplorerWsClient = Pick<WsClient, "request">;

export interface ExplorerSlice {
  entriesByDir: Record<string, FileEntryPayload[]>;
  expanded: Set<string>;
  loading: Set<string>;
  selected: string | null;
  openFile: string | null;
  sourceFile: string | null;
  error: Record<string, string>;
  viewMode: ExplorerViewMode;
  projectState: ProjectStatePayload | null;
  projectExpanded: Set<string>;
  projectSelected: string | null;
  projectLoading: boolean;
  projectError: string | null;
  loadDir: (client: ExplorerWsClient, dirPath: string) => Promise<void>;
  toggleExpand: (client: ExplorerWsClient, dirPath: string) => Promise<void>;
  setExpanded: (dirPaths: Set<string>) => void;
  select: (path: string | null) => void;
  open: (path: string) => void;
  openProjectFile: (path: string, nodeId?: string | null) => void;
  returnToSource: () => void;
  setViewMode: (mode: ExplorerViewMode) => void;
  loadProjectState: (client: ExplorerWsClient, sourceFile?: string | null) => Promise<void>;
  refreshProjectState: (client: ExplorerWsClient) => Promise<void>;
  setProjectExpanded: (nodeIds: Set<string>) => void;
  toggleProjectExpand: (nodeId: string) => void;
  selectProject: (nodeId: string | null) => void;
  addInclude: (
    client: ExplorerWsClient,
    targetPath: string,
    options?: { recursive?: boolean },
  ) => Promise<ProjectStatePayload>;
  removeIncludeEntry: (client: ExplorerWsClient, includePath: string) => Promise<ProjectStatePayload>;
  createPlan: (client: ExplorerWsClient, title: string) => Promise<ProjectStatePayload>;
  deletePlan: (client: ExplorerWsClient, planId: string) => Promise<ProjectStatePayload>;
  setActivePlan: (client: ExplorerWsClient, planId: string | null) => Promise<ProjectStatePayload>;
  setIncludeEntryActive: (
    client: ExplorerWsClient,
    includePath: string,
    active: boolean,
  ) => Promise<ProjectStatePayload>;
  setProjectChildActive: (
    client: ExplorerWsClient,
    childPath: string,
    active: boolean,
  ) => Promise<ProjectStatePayload>;
  createFile: (client: ExplorerWsClient, dirPath: string, name: string) => Promise<void>;
  createDirectory: (client: ExplorerWsClient, dirPath: string, name: string) => Promise<void>;
  createProjectFile: (client: ExplorerWsClient, name: string) => Promise<string>;
  createProjectDirectory: (client: ExplorerWsClient, name: string) => Promise<string>;
  expandToFile: (client: ExplorerWsClient, path: string) => Promise<void>;
  renameEntry: (client: ExplorerWsClient, path: string, newName: string) => Promise<void>;
  deleteEntry: (client: ExplorerWsClient, path: string) => Promise<void>;
  handleFileChanged: (client: ExplorerWsClient, event: FileChangedPayload) => void;
  reset: () => void;
}

export const useExplorerStore = create<ExplorerSlice>((set, get) => ({
  entriesByDir: {},
  expanded: new Set(),
  loading: new Set(),
  selected: null,
  openFile: null,
  sourceFile: null,
  error: {},
  viewMode: "tree",
  projectState: null,
  projectExpanded: new Set(),
  projectSelected: null,
  projectLoading: false,
  projectError: null,

  async loadDir(client, dirPath) {
    const dir = normalizeDirPath(dirPath);
    set((state) => ({
      loading: addSetValue(state.loading, dir),
      error: withoutKey(state.error, dir),
    }));

    try {
      const response = await client.request(
        "list_files",
        dir === ROOT_DIR ? {} : { subpath: dir },
      );
      const responseDir = normalizeDirPath(response.path);
      set((state) => ({
        entriesByDir: {
          ...state.entriesByDir,
          [responseDir]: sortEntries(response.entries.map(normalizeEntry)),
        },
        loading: removeSetValue(state.loading, dir),
        error: withoutKey(state.error, dir),
      }));
    } catch (error) {
      set((state) => ({
        loading: removeSetValue(state.loading, dir),
        error: {
          ...state.error,
          [dir]: formatExplorerError(error),
        },
      }));
    }
  },

  async toggleExpand(client, dirPath) {
    const dir = normalizeDirPath(dirPath);
    const expanded = new Set(get().expanded);
    if (expanded.has(dir)) {
      expanded.delete(dir);
      set({ expanded });
      return;
    }

    expanded.add(dir);
    set({ expanded });
    if (get().entriesByDir[dir] === undefined) {
      await get().loadDir(client, dir);
    }
  },

  setExpanded(dirPaths) {
    set({ expanded: new Set([...dirPaths].map(normalizeDirPath)) });
  },

  select(path) {
    set({ selected: path ? normalizePath(path) : null });
  },

  open(path) {
    const normalized = normalizePath(path);
    setLastOpenFile(normalized);
    set({
      selected: normalized,
      openFile: normalized,
      sourceFile: normalized,
      projectSelected: null,
      projectError: null,
    });
  },

  openProjectFile(path, nodeId = null) {
    const normalized = normalizePath(path);
    set({
      selected: normalized,
      openFile: normalized,
      projectSelected: nodeId,
      projectError: null,
    });
  },

  returnToSource() {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      return;
    }
    set({
      selected: sourceFile,
      openFile: sourceFile,
      projectSelected: null,
    });
  },

  setViewMode(mode) {
    set({ viewMode: mode });
  },

  async loadProjectState(client, sourceFileArg) {
    const sourceFile = normalizeOptionalPath(sourceFileArg ?? get().sourceFile);
    if (!sourceFile) {
      set({
        projectState: null,
        projectExpanded: new Set(),
        projectSelected: null,
        projectLoading: false,
        projectError: null,
      });
      return;
    }
    set({ projectLoading: true, projectError: null });
    try {
      const projectState = await client.request("get_project_state", { source_file: sourceFile });
      if (get().sourceFile !== sourceFile) {
        return;
      }
      set((state) => applyProjectStatePatch(state, projectState));
    } catch (error) {
      if (get().sourceFile !== sourceFile) {
        return;
      }
      set({
        projectLoading: false,
        projectError: formatExplorerError(error),
      });
    }
  },

  async refreshProjectState(client) {
    await get().loadProjectState(client, get().sourceFile);
  },

  setProjectExpanded(nodeIds) {
    set({ projectExpanded: new Set(nodeIds) });
  },

  toggleProjectExpand(nodeId) {
    set((state) => {
      const expanded = new Set(state.projectExpanded);
      if (expanded.has(nodeId)) {
        expanded.delete(nodeId);
      } else {
        expanded.add(nodeId);
      }
      return { projectExpanded: expanded };
    });
  },

  selectProject(nodeId) {
    set({ projectSelected: nodeId });
  },

  async addInclude(client, targetPath, options = {}) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before adding project includes.");
    }
    const projectState = await client.request("add_include", {
      source_file: sourceFile,
      target_path: normalizePath(targetPath),
      recursive: Boolean(options.recursive),
    });
    set((state) => applyProjectStatePatch(state, projectState));
    return projectState;
  },

  async removeIncludeEntry(client, includePath) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before removing project includes.");
    }
    const projectState = await client.request("remove_include_entry", {
      source_file: sourceFile,
      include_path: includePath,
    });
    set((state) => applyProjectStatePatch(state, projectState));
    return projectState;
  },

  async createPlan(client, title) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before creating a plan.");
    }
    const projectState = await client.request("create_plan", {
      source_file: sourceFile,
      title: title.trim() || "Untitled Plan",
    });
    const activePlan = findProjectPlanById(projectState.plans, projectState.active_plan_id);
    set((state) => ({
      ...applyProjectStatePatch(state, projectState),
      selected: activePlan ? normalizePath(activePlan.path) : state.selected,
      openFile: activePlan ? normalizePath(activePlan.path) : state.openFile,
      projectSelected: activePlan?.id ?? state.projectSelected,
    }));
    return projectState;
  },

  async deletePlan(client, planId) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before deleting a plan.");
    }
    const deletedPlan = findProjectPlanById(get().projectState?.plans ?? [], planId);
    const deletedPath = deletedPlan ? normalizePath(deletedPlan.path) : null;
    const projectState = await client.request("delete_plan", {
      source_file: sourceFile,
      plan_id: planId,
    });
    set((state) => {
      const reopenSource = deletedPath !== null && state.openFile === deletedPath && state.sourceFile !== null;
      return {
        ...applyProjectStatePatch(state, projectState),
        selected: reopenSource ? state.sourceFile : state.selected,
        openFile: reopenSource ? state.sourceFile : state.openFile,
      };
    });
    return projectState;
  },

  async setActivePlan(client, planId) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before changing the active plan.");
    }
    const projectState = await client.request("set_active_plan", {
      source_file: sourceFile,
      plan_id: planId,
    });
    set((state) => applyProjectStatePatch(state, projectState));
    return projectState;
  },

  async setIncludeEntryActive(client, includePath, active) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before changing include state.");
    }
    const projectState = await client.request("set_include_entry_active", {
      source_file: sourceFile,
      include_path: includePath,
      active,
    });
    set((state) => applyProjectStatePatch(state, projectState));
    return projectState;
  },

  async setProjectChildActive(client, childPath, active) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before changing project files.");
    }
    const projectState = await client.request("set_project_child_active", {
      source_file: sourceFile,
      child_path: normalizePath(childPath),
      active,
    });
    set((state) => applyProjectStatePatch(state, projectState));
    return projectState;
  },

  async createFile(client, dirPath, name) {
    const dir = normalizeDirPath(dirPath);
    const entryName = validateEntryName(name, { requireMarkdown: true });
    const path = joinPath(dir, entryName);
    const created = await client.request("create_file", { path });
    const newPath = normalizePath(created.path);
    setLastOpenFile(newPath);
    set({ selected: newPath, openFile: newPath, sourceFile: newPath });
    await get().loadDir(client, dir);
  },

  async createDirectory(client, dirPath, name) {
    const dir = normalizeDirPath(dirPath);
    const entryName = validateEntryName(name, { requireMarkdown: false });
    const path = joinPath(dir, entryName);
    const created = await client.request("create_directory", { path });
    set({ selected: normalizePath(created.path) });
    await get().loadDir(client, dir);
  },

  async createProjectFile(client, name) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before creating project files.");
    }
    const entryName = validateEntryName(name, { requireMarkdown: true });
    const dir = parentDir(sourceFile);
    const path = joinPath(dir, entryName);
    const created = await client.request("create_file", { path });
    const projectState = await client.request("add_include", {
      source_file: sourceFile,
      target_path: created.path,
      recursive: false,
    });
    set((state) => ({
      ...applyProjectStatePatch(state, projectState),
      selected: normalizePath(created.path),
      openFile: normalizePath(created.path),
      projectSelected:
        findProjectNodeByPath(projectState.entries, normalizePath(created.path), "entry")?.id ?? null,
    }));
    if (get().entriesByDir[dir] !== undefined) {
      await get().loadDir(client, dir);
    }
    return normalizePath(created.path);
  },

  async createProjectDirectory(client, name) {
    const sourceFile = get().sourceFile;
    if (!sourceFile) {
      throw new Error("Open a source file before creating project folders.");
    }
    const entryName = validateEntryName(name, { requireMarkdown: false });
    const dir = parentDir(sourceFile);
    const path = joinPath(dir, entryName);
    const created = await client.request("create_directory", { path });
    const projectState = await client.request("add_include", {
      source_file: sourceFile,
      target_path: created.path,
      recursive: true,
    });
    set((state) => ({
      ...applyProjectStatePatch(state, projectState),
      selected: normalizePath(created.path),
      projectSelected:
        findProjectNodeByPath(projectState.entries, normalizePath(created.path), "entry")?.id ?? null,
    }));
    if (get().entriesByDir[dir] !== undefined) {
      await get().loadDir(client, dir);
    }
    return normalizePath(created.path);
  },

  async expandToFile(client, path) {
    const normalized = normalizePath(path);
    const ancestors = ancestorDirs(normalized);
    if (ancestors.length === 0) return;
    set((state) => ({ expanded: new Set([...state.expanded, ...ancestors]) }));
    for (const dir of ancestors) {
      if (get().entriesByDir[dir] === undefined) {
        await get().loadDir(client, dir);
      }
    }
  },

  async renameEntry(client, path, newName) {
    const normalized = normalizePath(path);
    const result = await client.request("rename_entry", { path: normalized, new_name: newName });
    const newNormalized = normalizePath(result.path);
    const parent = parentDir(normalized);
    set((state) => {
      const patch: Partial<ExplorerSlice> = {};
      if (state.openFile === normalized) {
        patch.openFile = newNormalized;
        setLastOpenFile(newNormalized);
      }
      if (state.sourceFile === normalized) {
        patch.sourceFile = newNormalized;
      }
      if (state.selected === normalized) {
        patch.selected = newNormalized;
      }
      return patch;
    });
    await get().loadDir(client, parent);
  },

  async deleteEntry(client, path) {
    const normalized = normalizePath(path);
    await client.request("delete_entry", { path: normalized });
    const saved = getLastOpenFile();
    if (saved && isSelfOrChild(saved, normalized)) {
      clearLastOpenFile();
    }
    const parent = parentDir(normalized);
    set((state) => removeEntryPatch(state, normalized));
    await get().loadDir(client, parent);
  },

  handleFileChanged(client, event) {
    const path = normalizePath(event.path);
    const parent = parentDir(path);
    if (!event.exists || event.kind === "deleted") {
      set((state) => removeEntryPatch(state, path));
    }
    if (get().entriesByDir[parent] !== undefined) {
      void get().loadDir(client, parent);
    }
    if (get().sourceFile) {
      void get().loadProjectState(client, get().sourceFile);
    }
  },

  reset() {
    set({
      entriesByDir: {},
      expanded: new Set(),
      loading: new Set(),
      selected: null,
      openFile: null,
      sourceFile: null,
      error: {},
      viewMode: "tree",
      projectState: null,
      projectExpanded: new Set(),
      projectSelected: null,
      projectLoading: false,
      projectError: null,
    });
  },
}));

export function normalizeDirPath(path: string | null | undefined): string {
  if (!path || path === ".") {
    return ROOT_DIR;
  }
  return normalizePath(path);
}

export function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (trimmed === ".") {
    return ROOT_DIR;
  }
  return trimmed.replace(/^\.\/+/, "").replace(/\/+$/, "");
}

export function parentDir(path: string): string {
  const normalized = normalizePath(path);
  const index = normalized.lastIndexOf("/");
  return index === -1 ? ROOT_DIR : normalized.slice(0, index);
}

export function joinPath(dirPath: string, name: string): string {
  const dir = normalizeDirPath(dirPath);
  return dir ? `${dir}/${name}` : name;
}

function normalizeOptionalPath(path: string | null | undefined): string | null {
  return path ? normalizePath(path) : null;
}

function normalizeEntry(entry: FileEntryPayload): FileEntryPayload {
  return {
    ...entry,
    path: normalizePath(entry.path),
  };
}

function sortEntries(entries: FileEntryPayload[]): FileEntryPayload[] {
  return [...entries].sort((left, right) => {
    if (left.kind !== right.kind) {
      return left.kind === "dir" ? -1 : 1;
    }
    return left.name.localeCompare(right.name, undefined, { sensitivity: "base" });
  });
}

function validateEntryName(
  rawName: string,
  { requireMarkdown }: { requireMarkdown: boolean },
): string {
  const name = rawName.trim();
  if (!name) {
    throw new Error("Enter a name.");
  }
  if (name === "." || name === ".." || name.includes("/") || name.includes("\\")) {
    throw new Error("Enter a single file or folder name.");
  }
  if (new TextEncoder().encode(name).length > MAX_ENTRY_NAME_BYTES) {
    throw new Error(`Names must be ${MAX_ENTRY_NAME_BYTES} bytes or less.`);
  }
  if (requireMarkdown && !name.toLowerCase().endsWith(".md")) {
    throw new Error("New files must end with .md.");
  }
  return name;
}

function addSetValue(values: Set<string>, value: string): Set<string> {
  const next = new Set(values);
  next.add(value);
  return next;
}

function removeSetValue(values: Set<string>, value: string): Set<string> {
  const next = new Set(values);
  next.delete(value);
  return next;
}

function withoutKey(record: Record<string, string>, key: string): Record<string, string> {
  const next = { ...record };
  delete next[key];
  return next;
}

function removeEntryPatch(
  state: ExplorerSlice,
  path: string,
): Partial<ExplorerSlice> {
  const entriesByDir: Record<string, FileEntryPayload[]> = {};
  for (const [dir, entries] of Object.entries(state.entriesByDir)) {
    if (isSelfOrChild(dir, path)) {
      continue;
    }
    entriesByDir[dir] = entries.filter((entry) => !isSelfOrChild(entry.path, path));
  }

  const expanded = new Set([...state.expanded].filter((dir) => !isSelfOrChild(dir, path)));
  const loading = new Set([...state.loading].filter((dir) => !isSelfOrChild(dir, path)));
  const sourceCleared = state.sourceFile !== null && isSelfOrChild(state.sourceFile, path);
  return {
    entriesByDir,
    expanded,
    loading,
    selected:
      state.selected !== null && isSelfOrChild(state.selected, path) ? null : state.selected,
    openFile:
      state.openFile !== null && isSelfOrChild(state.openFile, path) ? null : state.openFile,
    sourceFile: sourceCleared ? null : state.sourceFile,
    projectState: sourceCleared ? null : state.projectState,
    projectExpanded: sourceCleared ? new Set() : state.projectExpanded,
    projectSelected: sourceCleared ? null : state.projectSelected,
    projectError: sourceCleared ? null : state.projectError,
  };
}

function applyProjectStatePatch(
  state: ExplorerSlice,
  projectState: ProjectStatePayload,
): Partial<ExplorerSlice> {
  const validIds = collectProjectItemIds(projectState);
  return {
    projectState,
    projectLoading: false,
    projectError: null,
    projectExpanded: new Set([...state.projectExpanded].filter((id) => validIds.has(id))),
    projectSelected:
      state.projectSelected && validIds.has(state.projectSelected) ? state.projectSelected : null,
  };
}

function collectProjectItemIds(projectState: ProjectStatePayload): Set<string> {
  const ids = new Set<string>();
  const stack = [...projectState.entries];
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node) {
      continue;
    }
    ids.add(node.id);
    stack.push(...node.children);
  }
  for (const plan of projectState.plans) {
    ids.add(plan.id);
  }
  return ids;
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
    if (normalizePath(node.path) === normalizePath(path) && (scope === undefined || node.scope === scope)) {
      return node;
    }
    stack.push(...node.children);
  }
  return null;
}

function findProjectPlanById(
  plans: ProjectPlanPayload[],
  planId: string | null,
): ProjectPlanPayload | null {
  if (!planId) {
    return null;
  }
  return plans.find((plan) => plan.plan_id === planId) ?? null;
}

function ancestorDirs(filePath: string): string[] {
  const parts = filePath.split("/");
  const ancestors: string[] = [];
  for (let i = 1; i < parts.length; i++) {
    ancestors.push(parts.slice(0, i).join("/"));
  }
  return ancestors;
}

function isSelfOrChild(candidate: string, parent: string): boolean {
  return candidate === parent || candidate.startsWith(`${parent}/`);
}

function formatExplorerError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function lastOpenFileStorageKey(): string {
  return getLastOpenFileStorageKey(getOrCreateBrowserInstanceId());
}

function getLastOpenFile(): string | null {
  return window.sessionStorage.getItem(lastOpenFileStorageKey());
}

function setLastOpenFile(path: string): void {
  window.sessionStorage.setItem(lastOpenFileStorageKey(), path);
}

function clearLastOpenFile(): void {
  window.sessionStorage.removeItem(lastOpenFileStorageKey());
}
