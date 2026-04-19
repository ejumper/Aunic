import { create } from "zustand";
import type { WsClient } from "../ws/client";
import type { FileChangedPayload, FileEntryPayload } from "../ws/types";

export const ROOT_DIR = "";

const LAST_OPEN_FILE_KEY = "aunic:lastOpenFile";

const MAX_ENTRY_NAME_BYTES = 255;

export type ExplorerWsClient = Pick<WsClient, "request">;

export interface ExplorerSlice {
  entriesByDir: Record<string, FileEntryPayload[]>;
  expanded: Set<string>;
  loading: Set<string>;
  selected: string | null;
  openFile: string | null;
  error: Record<string, string>;
  loadDir: (client: ExplorerWsClient, dirPath: string) => Promise<void>;
  toggleExpand: (client: ExplorerWsClient, dirPath: string) => Promise<void>;
  setExpanded: (dirPaths: Set<string>) => void;
  select: (path: string | null) => void;
  open: (path: string) => void;
  createFile: (client: ExplorerWsClient, dirPath: string, name: string) => Promise<void>;
  createDirectory: (client: ExplorerWsClient, dirPath: string, name: string) => Promise<void>;
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
  error: {},

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
    localStorage.setItem(LAST_OPEN_FILE_KEY, normalized);
    set({ selected: normalized, openFile: normalized });
  },

  async createFile(client, dirPath, name) {
    const dir = normalizeDirPath(dirPath);
    const entryName = validateEntryName(name, { requireMarkdown: true });
    const path = joinPath(dir, entryName);
    const created = await client.request("create_file", { path });
    const newPath = normalizePath(created.path);
    localStorage.setItem(LAST_OPEN_FILE_KEY, newPath);
    set({ selected: newPath, openFile: newPath });
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

  async deleteEntry(client, path) {
    const normalized = normalizePath(path);
    await client.request("delete_entry", { path: normalized });
    const saved = localStorage.getItem(LAST_OPEN_FILE_KEY);
    if (saved && isSelfOrChild(saved, normalized)) {
      localStorage.removeItem(LAST_OPEN_FILE_KEY);
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
  },

  reset() {
    set({
      entriesByDir: {},
      expanded: new Set(),
      loading: new Set(),
      selected: null,
      openFile: null,
      error: {},
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
  return {
    entriesByDir,
    expanded,
    loading,
    selected:
      state.selected !== null && isSelfOrChild(state.selected, path) ? null : state.selected,
    openFile:
      state.openFile !== null && isSelfOrChild(state.openFile, path) ? null : state.openFile,
  };
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
