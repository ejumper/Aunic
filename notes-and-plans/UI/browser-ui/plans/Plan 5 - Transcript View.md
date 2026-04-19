# Plan 5 — Transcript View

## Context
Plan 4 shipped the browser note editor (CodeMirror 6, markdown decorations, Mod-s save, revision-gated writes, external-change reload). The browser can now open and edit the `note-content` half of an Aunic file, but the `transcript` half — the table of chat/tool/search rows that the TUI renders prominently — is still invisible in the browser.

Plan 5 adds the browser transcript pane. It mirrors the TUI transcript in structure and semantics (per [browserUI-overview.md](../../UI/browser-ui/browserUI-overview.md#transcript)) while replacing TUI-cell rendering with real DOM rows that support native text selection. Row data is already delivered:
- `read_file` responses include `transcript_rows` and `has_transcript` ([messages.py:102](../../../src/aunic/browser/messages.py#L102))
- the backend broadcasts a `transcript_row` WS event on every `file_written` ProgressEvent ([session.py:411](../../../src/aunic/browser/session.py#L411))

No new live-event types are needed. Two new WS request handlers are: `delete_transcript_row` and `delete_search_result`, both thin wrappers over existing writer helpers.

After Plan 5, everything a run produces is visible and manageable in the browser; Plan 6 can then add the prompt editor and wire up run execution.

## Scope

### In scope
- Transcript pane component tree under `web/src/components/transcript/`
- Renderers for every existing `TranscriptRow` kind: `message` (chat bubble), `tool_result`/`tool_error` with tool-specific variants for `bash`, `web_search`, `web_fetch`, and a generic fallback for everything else
- Per-row expand/collapse for `bash` and `web_search` rows (matches TUI toggles)
- Per-row delete button
- Per-result delete for `web_search` rows (matches TUI, backed by `delete_search_result_item`)
- Filter toolbar: Chat / Tools / Search (matches TUI `TranscriptFilter`)
- Sort toggle: ascending / descending (TUI default: descending)
- Pane open/close `[v]` and maximize `[+]` controls (TUI parity)
- Drag-resize via CSS `resize: vertical` (min/max via CSS)
- Live append via subscription to `transcript_row` WS event
- Initial load from `transcript_rows` field of the snapshot already fetched by Plan 4
- Two new revision-gated backend WS handlers: `delete_transcript_row`, `delete_search_result`
- Native text selection / copy (no custom `y` keybinding, no right-click menu)
- Vitest unit + component tests; pytest for the two new handlers

### Deferred
- Indicator area wiring (Plan 6 owns; the transcript feeds it indirectly through progress events)
- Prompt editor and run submission (Plan 6)
- PWA / mobile gesture polish (Plan 7)
- Custom copy formats beyond the native selection (Plan 7 if ever)
- Virtualization — overview explicitly says no ([browserUI-overview.md:57](../../UI/browser-ui/browserUI-overview.md#L57))
- `tool_call` row rendering — TUI hides them, browser does the same

## Default policy decisions

Flip any of these at approval time:

| Decision | Default | Rationale |
|---|---|---|
| Pane position | Below `NoteEditor` in `workspace-main` column | Simplest; matches TUI vertical stack; scales to mobile |
| Drag-resize | CSS `resize: vertical` on pane container (min 12rem, max 80vh) | Zero-JS; browser-native; good enough for v1 |
| Default sort | `descending` (newest at top) | Matches TUI `TranscriptSortOrder` default ([types.py:45](../../../src/aunic/tui/types.py#L45)) |
| Default filter | `all` | Matches TUI |
| Maximize behavior | Covers `workspace-main`; `NoteEditor` hidden via `display: none` while maximized | Keeps editor state alive; no remount/data loss |
| Live-append auto-scroll | Only if user is within 64px of the current-edge (top for desc, bottom for asc) | Standard chat UX |
| Delete confirmation | None — immediate | Matches TUI (`Enter` on delete column triggers immediately) |
| Pane open default | Open when `has_transcript: true`, collapsed otherwise | Matches TUI initial state |
| Copy method | Native browser selection only | Overview prefers this over TUI `y` |
| Per-search-result delete | Included | TUI has it; backend helper already exists |
| Empty state | Muted "No transcript yet" placeholder when `has_transcript: false` | Consistent with editor empty state |

## Backend changes

### `src/aunic/browser/messages.py`
Extend `CLIENT_MESSAGE_TYPES` (currently [line 19](../../../src/aunic/browser/messages.py#L19)):
```python
CLIENT_MESSAGE_TYPES: frozenset[str] = frozenset({
    ...,
    "delete_transcript_row",
    "delete_search_result",
})
```

### `src/aunic/browser/session.py`
Add two methods alongside existing `write_file` (line 173):
- `delete_transcript_row(path, row_number, expected_revision)` — calls `delete_row_by_number` from [writer.py:198](../../../src/aunic/transcript/writer.py#L198). Re-uses the same revision gating and `RevisionConflict` raising pattern as `write_file`. Returns the updated `FileSnapshot` via `serialize_file_snapshot`.
- `delete_search_result(path, row_number, result_index, expected_revision)` — calls `delete_search_result_item` from the same writer module. Same revision gating; returns updated snapshot.

### `src/aunic/browser/connection.py`
Route the two new request types (follows existing pattern at [lines 127-162](../../../src/aunic/browser/connection.py#L127)):
- On `delete_transcript_row`: validate `path` against workspace root, call `session.delete_transcript_row(...)`, respond with `{snapshot}`. `RevisionConflict` → `{reason: "revision_conflict"}` (same shape Plan 4 already handles).
- On `delete_search_result`: same pattern.

No new server event types. The existing `transcript_row` and `file_changed` broadcasts fire automatically because the writer mutates the file.

### Tests: `tests/test_browser_session.py`
Add cases modeled on the existing `write_file` conflict test ([test_browser_session.py:90-150](../../../tests/test_browser_session.py#L90)):
- `delete_transcript_row` success removes the row, bumps revision
- `delete_transcript_row` with stale `expected_revision` raises `RevisionConflict`
- `delete_transcript_row` on unknown row number is a no-op that still returns current snapshot (matches writer behavior — confirm during impl)
- `delete_search_result` success removes the indexed result from a web_search row's content
- `delete_search_result` with stale revision raises `RevisionConflict`

## Frontend changes

### New deps
None. All CM6 packages already installed; transcript pane is plain React + zustand.

### `web/src/ws/types.ts`
Extend `ClientMessageType` union with `"delete_transcript_row" | "delete_search_result"`. Payload shapes:
```ts
type DeleteTranscriptRowRequest = { path: string; row_number: number; expected_revision: string };
type DeleteSearchResultRequest = { path: string; row_number: number; result_index: number; expected_revision: string };
```
Both return `{ snapshot: FileSnapshotPayload }` (same as `write_file`).

### `web/src/state/transcript.ts` (new Zustand slice)
```ts
interface TranscriptState {
  path: string | null;
  rows: TranscriptRowPayload[];         // snapshot + live merge, dedup by row_number
  revisionId: string | null;            // tracks latest snapshot revision for delete calls
  filterMode: "all" | "chat" | "tools" | "search";
  sortOrder: "ascending" | "descending";
  expandedRows: Set<number>;
  open: boolean;
  maximized: boolean;
  status: "idle" | "loading" | "deleting" | "error";
  error: string | null;

  loadFromSnapshot(snapshot: FileSnapshotPayload, path: string): void;
  applyLiveRow(event: TranscriptRowEventPayload): void;  // upsert by row_number
  applyFileChanged(change: FileChangedPayload, client: WsClient): Promise<void>; // reload rows after external change
  toggleExpand(rowNumber: number): void;
  setFilter(mode: TranscriptFilter): void;
  toggleSort(): void;
  toggleOpen(): void;
  toggleMaximized(): void;
  deleteRow(client: WsClient, rowNumber: number): Promise<void>;
  deleteSearchResult(client: WsClient, rowNumber: number, resultIndex: number): Promise<void>;
  reset(): void;
}
```
- `applyLiveRow` dedups on `row_number` — the backend uses row numbers as stable ids.
- `deleteRow` / `deleteSearchResult` pass `revisionId` as `expected_revision`, replace local state with the returned snapshot's rows + revision.
- On `file_changed` (path match), call `client.request("read_file", { path })` and replace `rows` + `revisionId`. Only triggers a reload if the change is an external modification, not our own delete echo (dedupe via `revisionId` match).

### `web/src/ws/context.tsx`
Current subscription (line ~56) dispatches `file_changed` to the note editor. Add parallel subscriptions:
```ts
client.on("transcript_row", (event) => useTranscriptStore.getState().applyLiveRow(event));
// extend existing file_changed handler to also call transcript store's applyFileChanged
```

### Components under `web/src/components/transcript/`

- **`TranscriptPane.tsx`** — top-level wrapper. Wires the store to the explorer's `openFile` (effect subscribes + fetches snapshot via Plan 4's `read_file` path; if NoteEditor already has a fresh snapshot via `noteEditor.revisionId`, reuse it to avoid a double fetch — see "Snapshot sharing" below). Renders `TranscriptToolbar` + scrollable `TranscriptList`. Applies `maximized` class (CSS covers workspace-main). Applies `resize: vertical` inline style.

- **`TranscriptToolbar.tsx`** — renders:
  - `[v]` / `[^]` open-close toggle
  - `[+]` / `[-]` maximize toggle
  - `Chat` / `Tools` / `Search` filter pills (aria-pressed)
  - Sort direction button (`↓ Descending` / `↑ Ascending`)

- **`TranscriptList.tsx`** — sorts `rows` by `row_number` respecting `sortOrder`, filters by `filterMode`, maps to row-specific components. Renders empty-state placeholder when filtered result is empty.

- **`rows/ChatRow.tsx`** — role-styled bubble (`user` / `assistant` / `system` variants). Content is markdown-like text — render as `<pre>` or with light markdown? **Default: plain text with `white-space: pre-wrap`**. Avoids pulling CM6 into transcript; user can upgrade later. Delete button on hover.

- **`rows/BashRow.tsx`** — collapsed view shows `$ {command}` with truncate-at-1-line. Expand reveals stdout/stderr blocks (monospace, `overflow: auto`, max-height capped with internal scroll), plus `exit_code` badge. Mirrors TUI limit semantics (TUI truncates to 25 lines each; browser can show full content since there's no cell grid — show full but cap container height).

- **`rows/SearchRow.tsx`** — header shows query; expand reveals result list. Each result is `{domain} · {title}` with `↗` link button (opens in new tab) and `✕` per-result delete button that calls `deleteSearchResult`.

- **`rows/FetchRow.tsx`** — title + link (`↗` opens URL in new tab). No expand/collapse needed.

- **`rows/ToolRow.tsx`** — generic fallback: tool name + pretty-printed JSON content. Collapsible for any content > N chars.

Each row has a consistent `RowShell` wrapper (role badge, delete button, per-row action slot). Keep the shell in a small helper (`rows/RowShell.tsx`) so markup stays uniform.

### Snapshot sharing with NoteEditor
Plan 4's `noteEditor` store already fetches the snapshot via `read_file`. To avoid a duplicate fetch:
- Expose the raw `FileSnapshotPayload` in `noteEditor` (or a lightweight derived selector) so `TranscriptPane` can subscribe and call `loadFromSnapshot(snapshot, path)` without a second `read_file` request.
- When `noteEditor` performs an optimistic write or accepts an external reload, it re-sets the snapshot → transcript pane auto-updates.
- `deleteRow` / `deleteSearchResult` both invalidate both stores — wire the response handler to feed the returned snapshot into both `noteEditor.setSnapshot(...)` and `transcript.loadFromSnapshot(...)`. Add a small shared helper (`state/snapshotBus.ts` or similar — or just dispatch to both stores from the call site — simpler).

**Default: add a shared helper** `applySnapshot(snapshot, path)` that updates both stores. Keeps each store focused on its half of the file.

### `web/src/App.tsx`
Insert `<TranscriptPane />` below `<NoteEditor />` inside `workspace-main`. The debug `<details>` stays at the bottom.

### `web/src/index.css`
Add styles for:
- `.transcript-pane` (container, resize handle, max-height, scroll)
- `.transcript-pane--maximized` (position: absolute over workspace-main)
- `.transcript-toolbar` + filter pills
- `.transcript-row` shell + role variants (`--user`, `--assistant`, `--system`, `--tool`)
- `.transcript-row__delete` (hover-reveal X button)
- `.transcript-row__body--bash`, `--search`, `--fetch`, `--tool` (per-kind layout)
- Collapsed vs expanded affordances (chevron, bordered body)

### Frontend tests

**`web/src/state/transcript.test.ts`** (vitest, jsdom):
- `loadFromSnapshot` populates rows, revision, open/closed based on `has_transcript`
- `applyLiveRow` upserts by `row_number` (dedupes, replaces older content)
- `toggleExpand`, `setFilter`, `toggleSort`, `toggleMaximized`, `toggleOpen` mutate state without side effects
- `deleteRow` calls `client.request("delete_transcript_row", ...)` with current `revisionId`, applies returned snapshot
- `deleteSearchResult` same pattern
- `applyFileChanged` reloads rows after external modification (mock client)

**`web/src/components/transcript/TranscriptPane.test.tsx`** (vitest + RTL-style assertions, using same mock pattern as `NoteEditor.test.tsx`):
- Empty state renders when `has_transcript: false`
- Snapshot with chat/bash/search rows renders distinct row variants
- Filter pill click narrows the list
- Sort toggle flips order
- Expand click on bash row reveals stdout block
- Delete button fires `delete_transcript_row` request
- `transcript_row` event appends a new row live

## Critical files

**Modify**
- [src/aunic/browser/messages.py](../../../src/aunic/browser/messages.py) — extend `CLIENT_MESSAGE_TYPES`
- [src/aunic/browser/session.py](../../../src/aunic/browser/session.py) — add `delete_transcript_row`, `delete_search_result`
- [src/aunic/browser/connection.py](../../../src/aunic/browser/connection.py) — route new requests
- [web/src/ws/types.ts](../../../web/src/ws/types.ts) — extend `ClientMessageType`, add request/response types
- [web/src/ws/context.tsx](../../../web/src/ws/context.tsx) — subscribe to `transcript_row`, extend `file_changed` handling
- [web/src/App.tsx](../../../web/src/App.tsx) — mount `<TranscriptPane />`
- [web/src/index.css](../../../web/src/index.css) — pane + row styles
- [web/src/state/noteEditor.ts](../../../web/src/state/noteEditor.ts) — expose full `snapshot` so transcript pane can subscribe (or add shared `applySnapshot` helper)

**Add**
- `web/src/state/transcript.ts`
- `web/src/state/snapshotBus.ts` (or inline helper — judgment call during impl)
- `web/src/components/transcript/TranscriptPane.tsx`
- `web/src/components/transcript/TranscriptToolbar.tsx`
- `web/src/components/transcript/TranscriptList.tsx`
- `web/src/components/transcript/rows/RowShell.tsx`
- `web/src/components/transcript/rows/ChatRow.tsx`
- `web/src/components/transcript/rows/BashRow.tsx`
- `web/src/components/transcript/rows/SearchRow.tsx`
- `web/src/components/transcript/rows/FetchRow.tsx`
- `web/src/components/transcript/rows/ToolRow.tsx`
- `web/src/state/transcript.test.ts`
- `web/src/components/transcript/TranscriptPane.test.tsx`

**Reference (reuse, do not modify)**
- [src/aunic/transcript/writer.py:198](../../../src/aunic/transcript/writer.py#L198) `delete_row_by_number`
- [src/aunic/transcript/writer.py](../../../src/aunic/transcript/writer.py) `delete_search_result_item`
- [src/aunic/browser/messages.py:102](../../../src/aunic/browser/messages.py#L102) `serialize_file_snapshot`
- [src/aunic/browser/messages.py:158](../../../src/aunic/browser/messages.py#L158) `serialize_transcript_row`
- [src/aunic/browser/session.py:411](../../../src/aunic/browser/session.py#L411) live `transcript_row` broadcast
- [src/aunic/tui/transcript_renderers.py](../../../src/aunic/tui/transcript_renderers.py) for per-kind rendering semantics to mirror

## Verification

**Automated**
- `cd web && npm run test` — new transcript tests plus all existing tests pass
- `cd web && npm run build` — tsc + vite build clean
- `cd web && npm run lint` — eslint clean
- `pytest tests/test_browser_session.py` — new `delete_transcript_row` + `delete_search_result` tests pass alongside existing

**Manual (12-step browser check)**
1. `aunic serve` backend + `cd web && npm run dev` frontend; open browser.
2. Open a note that has no transcript — pane shows collapsed with "No transcript yet" placeholder (or auto-collapsed per policy).
3. Open a note with mixed chat + bash + web_search rows — pane auto-opens; all three kinds render with role-styled bubbles and tool variants.
4. Click a bash row's chevron — stdout/stderr/exit_code reveal; click again — collapse.
5. Click a web_search row's chevron — results list reveals with `↗` and `✕` per result.
6. Click `↗` on a search result — opens the URL in a new tab.
7. Click `✕` on a search result — that result disappears; other rows untouched; editor shows updated content.
8. Select text across multiple rows and Cmd/Ctrl-C — native selection works.
9. Click filter `Chat` — only message rows show; toggle `Tools` — only tool results show; `All` — restores.
10. Click sort toggle — order flips; new rows (trigger a run in the TUI to produce one, or simulate via file write) append to the correct edge with auto-scroll when pinned.
11. Click row delete `✕` — row disappears in both pane and underlying file; transcript renumbers remaining rows.
12. Click `[+]` maximize — pane covers workspace-main; `[-]` restores; `[v]` collapses the pane header only.
13. Drag the bottom edge of the pane — resizes vertically within min/max bounds.
14. In another tab, modify the file manually to add a transcript row — pane shows the new row after `file_changed` reload.

## Done criteria
- [ ] All existing tests still green
- [ ] New vitest suites for transcript store + pane pass
- [ ] New pytest cases for `delete_transcript_row` + `delete_search_result` pass (including revision conflict)
- [ ] Manual checklist above completes cleanly on a file containing chat + bash + web_search + web_fetch rows
- [ ] No duplicate `read_file` fetch when NoteEditor already has the snapshot
- [ ] `transcript_row` live event appends without a full reload
- [ ] Native text selection across rows works (no `user-select: none` on row bodies)
- [ ] Pane position, resize, maximize behave per default policy table
