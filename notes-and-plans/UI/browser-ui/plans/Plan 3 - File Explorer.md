# Plan 3 — File Explorer

## Context

Plans 1 and 2 are finished: the backend serves a WebSocket session at `/ws` with typed message envelopes (`src/aunic/browser/connection.py:107`), and the web frontend (`web/`) has a typed `WsClient`, correlation map, auto-hello, reconnect, and a diagnostic app shell. The one file-touching UI today is `ReadFilePanel`, which requires the user to type a workspace path by hand.

Plan 3 replaces that typed-path flow with a real file explorer so the user can **browse**, **open**, **create** (folder or new markdown file), and **delete** (file or directory) entries inside the hardcoded workspace root — all over WebSocket, with every path validated server-side against the workspace scope.

End-state for this slice: a left-pane tree on the browser UI that lists the workspace, supports lazy expansion, reflects filesystem changes in real time (`file_changed` events already flow), and can open a file into `ReadFilePanel` by click. Create/delete are exposed as small toolbar buttons. Rename, drag/drop, and richer file ops are deferred to a later plan, matching the "conservative first implementation" guidance in `notes-and-plans/UI/browser-ui/browserUI-overview.md` (lines 74–80).

## Scope

**In Plan 3**
- Backend message types: `create_file`, `create_directory`, `delete_entry`. Routed in `connection.py:_dispatch`.
- `BrowserSession` gains `create_file`, `create_directory`, `delete_entry` methods. Path validation reuses `resolve_workspace_path` / `workspace_relative_path` (`src/aunic/browser/paths.py:12`).
- Wire types: extend `CLIENT_MESSAGE_TYPES` in `src/aunic/browser/messages.py:19`. No new server-pushed event types — the existing `file_changed` broadcast from `FileWatchHub` already covers creates/deletes.
- Frontend: `FileExplorer` component using **`react-aria-components`** `<Tree>` / `<TreeItem>`. Lazy loads children per directory via `list_files`, handles selection, open-on-click, and a small toolbar: **New file**, **New folder**, **Delete**, **Refresh**.
- Frontend state: a new `useExplorerStore` zustand slice caching `entriesByDir`, `expanded`, `selected`, `openFile`. Subscribes to `file_changed` via the existing `WsClient.on`.
- Replace `ReadFilePanel`'s path input with a read-only display of the tree-selected file; keep the snapshot summary rendering.
- App layout becomes two-pane: left = `FileExplorer`, right = existing panels stacked.
- Drift-control: update `tests/test_browser_wire_parity.py` to cover new client types.

**Deferred to later plans (not in Plan 3)**
- Rename/move — needs UX design for mobile; defer to Plan 7 or a standalone plan.
- Drag-and-drop reordering, drag-from-OS upload, multi-select — deferred.
- File icons beyond the emoji fallback (`📁` / `📄`).
- Filtering/search inside the tree — deferred.
- A "safe" trash/undo layer — deferred. Delete is permanent.
- Non-markdown file creation from the UI — v1 enforces `.md` suffix.

## Default policy choices (flag if you want these changed on approval)

| Decision | Default | Reason |
| --- | --- | --- |
| Create file extension | Must end with `.md` | Matches browserUI-overview.md End State "create new markdown files". Trivial to relax later. |
| Directory delete | Recursive (`shutil.rmtree`) | Matches normal file-manager expectation; forcing empty-first is friction, user already confirmed. |
| Delete confirmation | Native `window.confirm` | Good enough for v1; a styled modal is deferred UI polish. |
| Parent auto-create | Off | Explorer can only act inside already-visible/expanded dirs anyway — no ancestor inference needed. |
| Max name length | 255 bytes | Filesystem limit. Reject longer client-side and server-side. |
| Reserved names | `.` `..` `/` prefixes already blocked by `resolve_workspace_path` | No extra work needed. |
| Tree root label | Workspace root basename (e.g., `Aunic`) | Read from `session_state.workspace_root`. |

If any of these are wrong, say so on approval and I will flip them before writing code.

## Backend changes

### `src/aunic/browser/messages.py`

Extend `CLIENT_MESSAGE_TYPES`:

```python
CLIENT_MESSAGE_TYPES = frozenset({
    "hello", "list_files", "read_file", "write_file",
    "submit_prompt", "cancel_run", "resolve_permission",
    "create_file", "create_directory", "delete_entry",   # NEW
})
```

No new server message types — `file_changed` already covers propagation.

### `src/aunic/browser/session.py`

Add three coroutine methods on `BrowserSession`. All are `async def` and call `asyncio.to_thread` for filesystem work so the event loop stays responsive.

```python
async def create_file(self, subpath: str) -> dict[str, Any]:
    path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
    if path.suffix.lower() != ".md":
        raise BrowserError("invalid_extension", "Only .md files can be created from the browser.")
    if path.exists():
        raise BrowserError("already_exists", "A file or directory already exists at that path.")
    if not path.parent.exists() or not path.parent.is_dir():
        raise BrowserError("parent_not_found", "Parent directory does not exist.")
    await asyncio.to_thread(path.write_text, "", encoding="utf-8")
    snapshot = await self.file_manager.read_snapshot(path)
    return serialize_file_snapshot(snapshot, workspace_root=self.workspace_root)

async def create_directory(self, subpath: str) -> dict[str, Any]:
    path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
    if path.exists():
        raise BrowserError("already_exists", "A file or directory already exists at that path.")
    if not path.parent.exists() or not path.parent.is_dir():
        raise BrowserError("parent_not_found", "Parent directory does not exist.")
    await asyncio.to_thread(path.mkdir)  # parents=False, exist_ok=False
    return {"path": workspace_relative_path(path, workspace_root=self.workspace_root), "kind": "dir"}

async def delete_entry(self, subpath: str) -> dict[str, Any]:
    path = resolve_workspace_path(subpath, workspace_root=self.workspace_root)
    if not path.exists():
        raise BrowserError("not_found", "File or directory does not exist.")
    if path == self.workspace_root:
        raise BrowserError("refused", "Refusing to delete workspace root.")
    if path.is_dir():
        await asyncio.to_thread(shutil.rmtree, path)
        return {"path": workspace_relative_path(path, workspace_root=self.workspace_root), "kind": "dir"}
    await asyncio.to_thread(path.unlink)
    return {"path": workspace_relative_path(path, workspace_root=self.workspace_root), "kind": "file"}
```

No need to broadcast a separate event — `FileWatchHub` already emits `file_changed` for these filesystem transitions, and the frontend listens.

### `src/aunic/browser/connection.py`

Three more `_dispatch` branches, each a one-line delegation to the session method. Mirror the existing `_required_string` pattern for the `path` key.

```python
if envelope.type == "create_file":
    path = _required_string(payload, "path")
    await self.send_response(envelope.id, await self.session.create_file(path))
    return

if envelope.type == "create_directory":
    path = _required_string(payload, "path")
    await self.send_response(envelope.id, await self.session.create_directory(path))
    return

if envelope.type == "delete_entry":
    path = _required_string(payload, "path")
    await self.send_response(envelope.id, await self.session.delete_entry(path))
    return
```

### Backend tests

Add to `tests/test_browser_session.py`:
- `test_create_file_writes_empty_markdown`
- `test_create_file_rejects_non_md_suffix`
- `test_create_file_rejects_existing_path`
- `test_create_directory_creates_dir_and_rejects_existing`
- `test_delete_entry_removes_file`
- `test_delete_entry_removes_directory_recursively`
- `test_delete_entry_refuses_workspace_root`
- `test_create_file_rejects_path_escape` (via `resolve_workspace_path`'s existing `path_escape` error)

Add to `tests/test_browser_wire_parity.py`: assert the three new types are in `CLIENT_MESSAGE_TYPES` and covered by the TS-side parity snapshot.

## Frontend changes

### Dependency

Add to `web/package.json` dependencies:
- `react-aria-components` (latest 1.x) — ships the accessible `<Tree>` primitive, keyboard + touch support, selection manager.

### `web/src/ws/requests.ts`

Add three entries to `ClientRequestMap`:

```ts
create_file: {
  payload: { path: string };
  response: FileSnapshotPayload;
};
create_directory: {
  payload: { path: string };
  response: { path: string; kind: "dir" };
};
delete_entry: {
  payload: { path: string };
  response: { path: string; kind: "file" | "dir" };
};
```

### `web/src/state/explorer.ts` (new zustand slice)

```ts
interface ExplorerSlice {
  entriesByDir: Record<string, FileEntryPayload[]>;   // key "" is workspace root
  expanded: Set<string>;                              // dir paths
  loading: Set<string>;                               // dir paths currently fetching
  selected: string | null;                            // selected entry path
  openFile: string | null;                            // currently-opened file path
  error: Record<string, string>;                      // per-dir error messages

  loadDir: (client: WsClient, dirPath: string) => Promise<void>;
  toggleExpand: (client: WsClient, dirPath: string) => Promise<void>;
  select: (path: string | null) => void;
  open: (path: string) => void;
  createFile: (client: WsClient, dirPath: string, name: string) => Promise<void>;
  createDirectory: (client: WsClient, dirPath: string, name: string) => Promise<void>;
  deleteEntry: (client: WsClient, path: string) => Promise<void>;
  handleFileChanged: (client: WsClient, evt: FileChangedPayload) => void;
  reset: () => void;
}
```

Design notes:
- `entriesByDir[""]` is the workspace root listing, matching `list_files({})` which returns `path: "."`. Normalize `"."` to `""` on the client for clean dictionary keys.
- `handleFileChanged` finds the parent dir of `evt.path`, checks whether it's in `entriesByDir`, and if so re-fires `loadDir` for that parent. Cheap and drift-free.
- `createFile`/`createDirectory` optimistically call `loadDir` on the parent after the backend ack, so the new entry appears even if the watcher is slow.
- `deleteEntry` removes the path from state immediately on ack, then `loadDir` on parent to reconcile.

### `web/src/components/FileExplorer.tsx` (new)

A `<Tree>` from `react-aria-components` driven by `useExplorerStore`.

Structure:
- Toolbar: `[ + File ]` `[ + Folder ]` `[ Delete ]` `[ Refresh ]` buttons. Enabled/disabled based on selection.
- `<Tree aria-label="Workspace" selectionMode="single" onSelectionChange={...}>` with items recursively rendered from `entriesByDir`.
- Each `<TreeItem>` renders `📁 {name}` for dirs, `📄 {name}` for files.
- On expand of a dir, call `loadDir`. Show a small spinner glyph while `loading` contains the dir path.
- On file click (selection change on a `file` kind), call `openFile(path)` which also triggers `read_file` via the existing `ReadFilePanel` (see refactor below).
- Create flows use a single-line inline input placed under the target dir (mobile-friendly). Submit with Enter; Esc cancels. No modal for v1.
- Delete uses `window.confirm(`Delete "${name}"? This cannot be undone.`)`.

Keyboard behavior is provided by React Aria out of the box (Arrow keys, Enter, Home/End).

Mobile: `react-aria-components` handles touch; the toolbar buttons stack under the tree at narrow widths (CSS media query).

### `web/src/components/ReadFilePanel.tsx` (refactor)

- Remove the free-form path input.
- Consume `openFile` from `useExplorerStore`. When it changes, fetch `read_file` and render the existing snapshot summary.
- Keep existing error rendering.

### `web/src/App.tsx` (refactor)

Two-pane layout:

```
+----------------------+-----------------------------+
|  FileExplorer        |  ConnectionBadge            |
|                      |  HelloPanel                 |
|                      |  ReadFilePanel (snapshot)   |
+----------------------+-----------------------------+
|              RawLog (full width, bottom)           |
+----------------------------------------------------+
```

CSS in `web/src/styles/app.css`: grid two columns (`minmax(260px, 320px) 1fr`), collapsing to a stacked layout under `max-width: 720px` (mobile first-class).

### `web/src/ws/context.tsx`

On `WsProvider` mount, wire one `client.on("file_changed", ...)` subscription that forwards to `useExplorerStore.getState().handleFileChanged(client, payload)`. Unsubscribe on unmount.

Also on first successful `session_state`, kick off `useExplorerStore.getState().loadDir(client, "")` so the root is listed without user action.

### Frontend tests

Add to `web/src/state/explorer.test.ts` using a mock `WsClient` exposing `request` + `on`:
- `loadDir` stores entries, clears loading state
- `toggleExpand` expands, lazy-loads, then collapses
- `createFile` calls backend and refreshes parent
- `deleteEntry` removes from state on ack
- `handleFileChanged` re-fetches the affected dir when that dir is cached

Component test for `FileExplorer` can be deferred to Plan 7 Playwright smoke tests — unit tests on the store cover the critical logic.

## Critical files

**Modify (backend)**
- [src/aunic/browser/messages.py](src/aunic/browser/messages.py) — expand `CLIENT_MESSAGE_TYPES`
- [src/aunic/browser/session.py](src/aunic/browser/session.py) — add `create_file`, `create_directory`, `delete_entry`
- [src/aunic/browser/connection.py](src/aunic/browser/connection.py) — three new `_dispatch` branches
- [tests/test_browser_session.py](tests/test_browser_session.py) — new session tests
- [tests/test_browser_wire_parity.py](tests/test_browser_wire_parity.py) — parity with TS side

**Modify (frontend)**
- [web/package.json](web/package.json) — add `react-aria-components`
- [web/src/ws/requests.ts](web/src/ws/requests.ts) — three new request types
- [web/src/App.tsx](web/src/App.tsx) — two-pane layout
- [web/src/components/ReadFilePanel.tsx](web/src/components/ReadFilePanel.tsx) — drop free-form input, consume explorer store
- [web/src/styles/app.css](web/src/styles/app.css) — two-pane grid + responsive stacking
- [web/src/ws/context.tsx](web/src/ws/context.tsx) — wire `file_changed` subscription + initial `loadDir`

**Add (frontend)**
- [web/src/state/explorer.ts](web/src/state/explorer.ts) — `useExplorerStore`
- [web/src/state/explorer.test.ts](web/src/state/explorer.test.ts) — store unit tests
- [web/src/components/FileExplorer.tsx](web/src/components/FileExplorer.tsx) — React Aria Tree + toolbar

**Do not modify**
- [src/aunic/browser/paths.py](src/aunic/browser/paths.py) — existing validation is sufficient
- [src/aunic/browser/watch_hub.py](src/aunic/browser/watch_hub.py) — already fans out `file_changed`
- [src/aunic/context/file_manager.py](src/aunic/context/file_manager.py) — filesystem ops are one-line enough to live in `session.py` without polluting the shared `FileManager`
- [web/src/ws/client.ts](web/src/ws/client.ts) — API is already general enough

## Verification

End-to-end manual:
1. `uv run aunic serve --host 127.0.0.1 --port 8765 --workspace /home/ejumps` (or whatever the current serve CLI expects)
2. In `web/`: `pnpm dev` (or `npm run dev`)
3. Open the Vite URL in a browser
4. Confirm the workspace root lists on load
5. Expand a directory — entries should appear
6. Click `+ File`, enter `scratch.md`, press Enter — new file appears under selected dir, `ReadFilePanel` shows the empty snapshot
7. Click `+ Folder`, enter `scratch-dir` — folder appears
8. `touch` a new file outside the browser — tree updates automatically via `file_changed`
9. Select a file, click `Delete`, confirm — file disappears in tree and from filesystem
10. Select `scratch-dir`, click `Delete` — directory and any contents are removed

Automated:
- `uv run pytest tests/test_browser_session.py tests/test_browser_wire_parity.py -q`
- `cd web && pnpm test` (vitest, including new `explorer.test.ts`)
- `cd web && pnpm build` (type-check + production build should pass)

## Done criteria

- Tree renders the workspace on first connect, lazy expands, handles keyboard and touch.
- Create file, create folder, delete file, delete folder all round-trip over WS and reflect in the tree without a manual refresh.
- External filesystem changes are mirrored in the tree within the watcher's debounce window.
- Selecting a file opens its snapshot in `ReadFilePanel`; no free-form path input remains anywhere in the UI.
- All backend + frontend tests pass; wire parity test covers the three new client message types.
- No new server-side message types invented (we stay on `file_changed`).
