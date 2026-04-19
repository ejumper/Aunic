# Plan 4 — Note Editor (CodeMirror 6)

## Context

Plans 1–3 built the backend WebSocket server, the frontend Vite+React+TS scaffold with typed `WsClient`, and a working `FileExplorer` over `react-aria-components` Tree. Selecting a file in the explorer currently populates `useExplorerStore.openFile` and a read-only `ReadFilePanel` displays the snapshot's metadata.

Plan 4 replaces that read-only panel with a real browser note editor that mirrors the TUI's note-editing behavior: CodeMirror 6 integrated imperatively, source-text markdown with live-rendered decorations (suppressed on the active line), edit-command marker highlighting, soft word wrapping, Obsidian-style folding (with the "search results" / "work log" managed-section auto-fold), revision-aware save, and external-change reload. The backend already exposes everything the editor needs — `read_file` returns `note_content + revision_id`, `write_file` takes `expected_revision` and raises `revision_conflict`, and the existing `file_changed` broadcast fires on external edits — so Plan 4 is almost entirely a frontend build.

Per [browserUI-overview.md](../../UI/browser-ui/browserUI-overview.md), the shared CodeMirror wrapper created here will be reused by the prompt editor in Plan 6. Plan 4 only stubs the `save-on-send` entry point; Plan 6 wires the actual send path.

## Scope

**In scope**
- CodeMirror 6 deps + imperative `CodeMirrorHost` wrapper (shared, reused later by Plan 6 prompt editor)
- `NoteEditor` component that loads the active file, tracks dirty state, handles save
- `useNoteEditorStore` Zustand slice: revision id, dirty flag, save status, conflict/reload banners
- Live markdown rendering with active-line syntax un-hiding
- Edit-command marker decorations for the 4 pairs in [markers.py:19](../../../src/aunic/context/markers.py#L19)
- Soft word wrap preserving indentation
- Folding: heading-based via `@codemirror/lang-markdown`, plus managed-section auto-fold for "search results" / "work log" (mirrors [folding.py:11](../../../src/aunic/tui/folding.py#L11))
- Revision-aware save on `Mod-s`; `revision_conflict` → reload/keep banner
- External `file_changed` reload: if clean, swap buffer silently; if dirty, show reload/keep banner
- Table visual validation (prototype highest-risk path per overview)
- Replace `ReadFilePanel` with `NoteEditor` in `App.tsx`, delete the old panel
- Save-on-send scaffolding: expose `noteEditorStore.saveIfDirty()` returning `Promise<boolean>` for Plan 6 to call

**Out of scope (later plans)**
- Prompt editor (Plan 6) — but Plan 4 builds the shared `CodeMirrorHost` it will reuse
- Transcript view (Plan 5) — editor only sees `note_content`, never the transcript
- Actual prompt send/run wiring (Plan 6)
- Any rich-text / preview-only mode — source-text editing only
- Custom table rendering beyond what `@codemirror/lang-markdown` provides out of the box
- Extended Aunic keybindings (only `Mod-s` lands now; send/run lands in Plan 6)
- Diff view or per-file undo history persistence across reloads

## Default policy decisions (flip any of these at approval time)

| Decision | Default |
|---|---|
| Save keybinding | `Mod-s` (Ctrl on Win/Linux, Cmd on mac). Intercept browser Save-As. |
| Auto-save on blur / interval | **Off.** Save is explicit (`Mod-s`) or via save-on-send. Matches TUI. |
| External reload when clean | Silent swap, banner says "Reloaded from disk." for 3s. |
| External reload when dirty | Banner with `Reload (discard my edits)` / `Keep mine` buttons. Mirrors [controller.py:656](../../../src/aunic/tui/controller.py#L656). |
| Revision conflict on save | Banner with `Reload their version` / `Overwrite (force save)` / `Cancel`. Force save = re-read, then retry with new revision. |
| Managed section auto-fold | Applied on initial load only. Re-applied after external reload. Not re-applied on user-triggered reload. |
| Active-line markdown un-hide | Per-line (whole line of cursor), not per-block. Simpler, closer to Obsidian live preview. |
| Edit-marker decoration colors | Match TUI rendering roles: write=blue, include=green, exclude=red, read_only=amber. Exact hex via CSS vars in `index.css`. |
| Tables | Rely on `@codemirror/lang-markdown` + GFM parser. Add visual check to manual verification. No custom table extension. |

## Backend changes

**None required.** The backend already exposes:
- `read_file` → [session.py:168](../../../src/aunic/browser/session.py#L168) returns `FileSnapshotPayload` with `note_content` + `revision_id`
- `write_file` → [session.py:173](../../../src/aunic/browser/session.py#L173) accepts `expected_revision`, raises `RevisionConflict` on mismatch
- `file_changed` broadcast → already wired through `FileWatchHub`

If testing surfaces a missing response field (e.g. server-side normalized path), land that fix as a follow-up — not part of Plan 4.

## Frontend changes

### `web/package.json` — new deps
```
@codemirror/view
@codemirror/state
@codemirror/language
@codemirror/commands
@codemirror/search
@codemirror/lang-markdown
@codemirror/theme-one-dark   (or write a tiny local theme instead — see note)
@lezer/highlight
```
Optionally a local theme (skip `theme-one-dark` if a custom Aunic palette is preferred). Use the `@codemirror/*` packages directly; do **not** add `react-codemirror` / `@uiw/react-codemirror` — we want the imperative wrapper to stay thin and reusable for Plan 6's prompt editor.

### `web/src/components/editor/CodeMirrorHost.tsx` (new — shared, ~80 LOC)

A thin imperative React wrapper. Signature:
```ts
interface CodeMirrorHostProps {
  initialDoc: string;           // only read once; changes after mount use setDoc() imperatively
  extensions: Extension[];       // immutable after mount; re-mount if changed
  onReady?: (view: EditorView) => void;
  onDocChanged?: (doc: string, tr: Transaction) => void;
  className?: string;
  ariaLabel?: string;
}
```
Responsibilities:
- `useRef<HTMLDivElement>` + `useEffect` creates `EditorView` once
- Teardown on unmount
- Expose view via `onReady` so parent can imperatively call `view.dispatch(...)`, `setDoc(...)`, etc.
- `onDocChanged` fires from a `EditorView.updateListener.of(...)` extension that parent appends

Never re-render the editor through React state changes to the `initialDoc` prop — that's how controlled-CM wrappers go wrong. Document placement updates go through `view.dispatch({ changes: ... })`.

### `web/src/components/editor/NoteEditor.tsx` (new)

Consumes `useExplorerStore.openFile`; owns `useNoteEditorStore` lifecycle:

1. When `openFile` changes: clear prior state, call `client.request("read_file", { path })`, then construct extension list, then mount `CodeMirrorHost` with `initialDoc = snapshot.note_content`.
2. Keep a ref to the `EditorView`; subscribe `updateListener` → pushes doc updates into `noteEditorStore.markDirty()`.
3. Exposes a handler for the `Mod-s` keymap → `noteEditorStore.save(viewRef.current.state.doc.toString())`.
4. Renders save/dirty indicator, conflict banner, reload banner, save error.

Extension list (order matters):
```
[
  markdown({ codeLanguages: languages }),
  EditorView.lineWrapping,         // soft wrap
  softWrapIndent(),                // custom CSS extension (below)
  editCommandMarkersExt(),         // custom (below)
  activeLineRawMarkdown(),         // custom (below)
  foldGutter(),
  codeFolding(),
  keymap.of([{ key: "Mod-s", preventDefault: true, run: (view) => { saveHandler(); return true } }, ...defaultKeymap, ...historyKeymap, ...foldKeymap, ...searchKeymap]),
  history(),
  highlightActiveLine(),
  EditorView.updateListener.of(...),
  aunicTheme(),                    // CSS vars-based theme
]
```

### `web/src/components/editor/extensions/` (new subdirectory)

- **`editCommandMarkers.ts`** — `ViewPlugin` with `MatchDecorator` matching the 8 tokens from [markers.py:19](../../../src/aunic/context/markers.py#L19). Each opener/closer gets a `Decoration.mark({ class: "cm-aunic-marker cm-aunic-marker-<kind>" })`. CSS colors match TUI palette. Because marker pairs nest, match by token not by regex-scoped pairs — the decoration is on the tokens themselves, not the spans between them.

- **`activeLineRawMarkdown.ts`** — filters out markdown decorations (bold `**`, italic `*`/`_`, link brackets, heading `#`, etc.) on the current cursor line. Strategy: instead of fighting the built-in lang-markdown decorations, layer a `hideMarkup` extension:
  - Default state: hide markdown punctuation via `Decoration.replace({})` / `Decoration.mark({ class: "cm-aunic-hide" })` with CSS `display: none` or `font-size: 0`
  - On selection change: exclude the current line's byte range from hidden decorations
  - This is the standard Obsidian-style live-preview pattern; implement incrementally: bold/italic/headings first, then links if time allows.

- **`softWrapIndent.ts`** — CSS-only extension that sets `.cm-line { text-indent: calc(-1ch); padding-left: 1ch; }` plus `white-space: break-spaces`. If visual indentation is wrong for nested lists, upgrade to per-line `indent-wrapped-lines` decoration later.

- **`managedSectionAutoFold.ts`** — a one-shot `StateEffect` dispatched after mount (and after external reload). Walks syntax tree for ATXHeadings whose heading text (case-folded) is in `{"search results", "work log"}`, dispatches `foldEffect.of({ from, to })` for each region from the end of the heading line to the next same-or-higher-level heading. Mirrors [folding.py:11,50-57](../../../src/aunic/tui/folding.py#L11).

- **`aunicTheme.ts`** — `EditorView.theme({})` with CSS vars: background, gutter, active line, marker colors, hidden markup. One file, short.

### `web/src/state/noteEditor.ts` (new)

```ts
interface NoteEditorSlice {
  path: string | null;
  revisionId: string | null;
  initialDoc: string;
  dirty: boolean;
  status: "idle" | "loading" | "saving" | "error";
  error: string | null;
  conflict: null | { remoteRevisionId: string; remoteDoc: string };   // set on revision_conflict or external-dirty
  externalReloadPending: null | { reason: "external-changed"; snapshot: FileSnapshotPayload };

  // actions (async ones take the WsClient by DI; consistent with explorer.ts)
  loadForPath(client, path): Promise<void>;
  markDirty(nextDoc: string): void;       // called by NoteEditor on every doc change
  save(client, currentDoc: string): Promise<void>;
  saveIfDirty(client, currentDoc: string): Promise<boolean>;   // used later by Plan 6 save-on-send
  handleExternalChange(client, change: FileChangedPayload): Promise<void>;
  resolveConflict(strategy: "reload" | "overwrite"): Promise<void>;
  reset(): void;
}
```

- `markDirty` compares against `initialDoc` byte-for-byte to decide `dirty` (not just "changed since last save", so undo-to-original clears the flag).
- `save` sends `write_file` with `expected_revision: revisionId`. On success, updates `revisionId`, `initialDoc`, clears `dirty`. On `revision_conflict` (from `WsRequestError.reason`), fetches current snapshot and stores `conflict` for banner rendering.
- `handleExternalChange` checks `change.path === path`. If `change.revision_id === revisionId` → no-op (echo from our own save). If `dirty` → stash snapshot in `externalReloadPending` for banner. Else → silently replace `initialDoc` and `revisionId`, trigger `NoteEditor` to `view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: snapshot.note_content } })`.

### `web/src/ws/context.tsx` — wire `file_changed` subscription

In `WsProvider`, add:
```ts
useEffect(() => {
  return client.on("file_changed", (payload) => {
    void useExplorerStore.getState().handleFileChanged(client, payload);
    void useNoteEditorStore.getState().handleExternalChange(client, payload);
  });
}, [client]);
```
(The explorer handler is already wired; this adds a second consumer to the same subscription.)

### `web/src/components/ReadFilePanel.tsx` — delete
`App.tsx` swaps the panel out for `<NoteEditor />`. Keep nothing for "backwards compat" — the snapshot metadata it showed is debug-only and not worth preserving.

### `web/src/App.tsx` — layout tweak
Replace the `panel-grid` containing `HelloPanel` + `ReadFilePanel` with a stack that places `NoteEditor` as the primary workspace content. `HelloPanel` can stay below or move to a debug pane; default: keep it, move below editor in a collapsed "Debug" `<details>` (low-cost, preserves the Plan 2 debug affordance without cluttering).

### `web/src/index.css`
Add: CSS vars for marker colors, `.cm-aunic-marker-*` rules, hidden-markup rule, editor container sizing (flex-grow, min-height), dirty indicator, banner styles.

## Tests

### Frontend — `web/src/state/noteEditor.test.ts` (new)
- `loadForPath` populates `initialDoc` / `revisionId`, clears conflict
- `markDirty` sets `dirty=true` when doc diverges from `initialDoc`; clears `dirty` when user undoes back to initial
- `save` sends `write_file` with correct `expected_revision`, updates state on success
- `save` sets `conflict` on `revision_conflict` error
- `saveIfDirty` short-circuits to `true` when `!dirty`
- `handleExternalChange` no-ops on echo (matching revision)
- `handleExternalChange` silently swaps when clean
- `handleExternalChange` sets `externalReloadPending` when dirty
- `resolveConflict("reload")` replaces `initialDoc`, clears banner
- `resolveConflict("overwrite")` re-sends `write_file` with the remote revision as `expected_revision`

### Frontend — `web/src/components/editor/NoteEditor.test.tsx` (new, vitest + jsdom)
- Renders empty state when `openFile` is null
- Calls `read_file` on `openFile` change, mounts editor with returned `note_content`
- `Mod-s` dispatches `write_file`
- Dirty indicator visible when `markDirty` fires
- Reload banner visible when `externalReloadPending` set

(Extension-level tests — `editCommandMarkers`, `activeLineRawMarkdown`, `managedSectionAutoFold` — are integration-verified via visual check. CM6 extensions are tedious to unit-test; save that bandwidth.)

### Backend — no new tests required
Existing [test_browser_session.py](../../../tests/test_browser_session.py) covers `write_file` revision conflicts, note/transcript preservation, and file-change broadcasting. Plan 4 adds no backend surface.

## Critical files

**Modify**
- [web/package.json](../../../web/package.json) — add 7 deps
- [web/src/App.tsx](../../../web/src/App.tsx) — replace `ReadFilePanel` with `NoteEditor`
- [web/src/ws/context.tsx](../../../web/src/ws/context.tsx) — add `file_changed` subscriber for note editor
- [web/src/index.css](../../../web/src/index.css) — editor theme, banners, marker colors
- [web/src/components/errors.ts](../../../web/src/components/errors.ts) — may need `isWsRequestError` helper reuse; no change likely

**Add**
- `web/src/components/editor/CodeMirrorHost.tsx`
- `web/src/components/editor/NoteEditor.tsx`
- `web/src/components/editor/extensions/editCommandMarkers.ts`
- `web/src/components/editor/extensions/activeLineRawMarkdown.ts`
- `web/src/components/editor/extensions/softWrapIndent.ts`
- `web/src/components/editor/extensions/managedSectionAutoFold.ts`
- `web/src/components/editor/extensions/aunicTheme.ts`
- `web/src/state/noteEditor.ts`
- `web/src/state/noteEditor.test.ts`
- `web/src/components/editor/NoteEditor.test.tsx`

**Delete**
- [web/src/components/ReadFilePanel.tsx](../../../web/src/components/ReadFilePanel.tsx)

**Do not modify** (reference only)
- [src/aunic/browser/session.py](../../../src/aunic/browser/session.py)
- [src/aunic/context/markers.py](../../../src/aunic/context/markers.py)
- [src/aunic/tui/folding.py](../../../src/aunic/tui/folding.py)
- [src/aunic/context/file_manager.py](../../../src/aunic/context/file_manager.py)

## Verification

### Automated
- `cd web && npm install` — pulls new CM6 deps
- `cd web && npm run test` — runs `noteEditor.test.ts` + `NoteEditor.test.tsx` + existing tests
- `cd web && npm run build` — tsc + vite build (catches type regressions)
- `cd web && npm run lint`
- `uv run pytest` — confirms backend tests still pass (no backend changes expected)

### Manual (the real validation for editor work)
Run `uv run aunic serve` + `cd web && npm run dev` in two terminals.

1. **Open + render** — click a markdown file in the explorer. Editor mounts, cursor lands in content. Headings render as larger text; bold/italic syntax hidden except on cursor line.
2. **Edit markers** — open a file containing `@>> ... <<@` etc. Markers show colored. Add a new marker; decoration appears live.
3. **Tables** — open (or create) a note with a GFM pipe table. Confirm the table renders without mangling the source. (Highest-risk path per overview.)
4. **Soft wrap** — resize window narrow; long lines wrap; wrapped continuations align under the start of the wrapped text (or at least don't regress to column 0 breaking list visual).
5. **Folding** — click heading fold gutter; section collapses. Create/open a note with a `## Search Results` or `## Work Log` heading and content beneath; confirm that section is folded on initial load.
6. **Save** — edit → `Mod-s` → save completes, dirty indicator clears, revision_id updates in devtools.
7. **Save-on-send scaffolding** — in devtools console, run `useNoteEditorStore.getState().saveIfDirty(wsClient, currentDoc)` → returns `true` with no work when clean; saves when dirty.
8. **Revision conflict** — edit in browser. In a second terminal: `echo "external" >> <file>`. Press `Mod-s`. Banner appears with reload/overwrite/cancel. Reload → buffer replaced. Overwrite → save succeeds.
9. **External reload clean** — with browser editor open and clean, edit file externally. Editor buffer updates silently within ~1s of the save.
10. **External reload dirty** — with browser editor dirty, edit file externally. Banner appears. Reload discards edits; Keep mine preserves and re-arms save (next `Mod-s` will revision-conflict — correct behavior, user chose it).
11. **Echo suppression** — saving in the browser fires `file_changed`; confirm banner does NOT appear (echo suppressed by revision match).
12. **Switch files** — select a different file while dirty. Confirm we either (a) prompt the user, or (b) discard and load new file. Default pick: discard and load (simplest; the explorer click is an intentional navigation). Revisit if user feedback says otherwise.

### Mobile
- Open browser UI on iPhone Safari over LAN. Tap a file. Confirm: keyboard appears, can type, selection works, fold gutter is tappable. Defer iOS polish to Plan 7.

## Done criteria

- [ ] Selecting a file in the explorer loads it into a CodeMirror editor (not `ReadFilePanel`)
- [ ] Typing updates the buffer with no network round-trips per keystroke
- [ ] `Mod-s` saves via `write_file` with `expected_revision`
- [ ] Markdown syntax renders, except on the active line (raw syntax visible there)
- [ ] The 4 edit-command marker pairs are colored
- [ ] `search results` / `work log` headings auto-fold on initial load
- [ ] GFM tables render visibly without syntax corruption
- [ ] External file edits reload the buffer when clean; show a banner when dirty
- [ ] Revision conflicts show a banner with reload / overwrite / cancel options
- [ ] `useNoteEditorStore.saveIfDirty()` exported for Plan 6 to consume
- [ ] `ReadFilePanel.tsx` deleted; `App.tsx` uses `NoteEditor`
- [ ] `npm run test`, `npm run build`, `npm run lint`, `pytest` all pass
