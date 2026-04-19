# Plan 6 — Prompt Editor + Run

## Context

Plans 1–5 built the browser-side equivalents of the TUI's file tree, note editor,
and transcript. Today the browser can open a file, edit it with CodeMirror,
and see its transcript update live — but the user cannot actually *drive* a run
from the browser. Aunic-the-TUI is still the only way to submit a prompt, switch
mode/work-mode, pick a model, or respond to a permission prompt.

Plan 6 closes that loop so the browser becomes a usable remote control:

- A CodeMirror 6 prompt editor sharing the same host as the note editor, with
  prompt-specific extensions (syntax highlight for `@web` / `@rag` / slash
  commands / edit markers, static autocomplete, Ctrl/Cmd+Enter to send).
- A send/cancel flow over existing `submit_prompt` / `cancel_run` WS types,
  with save-on-send wired to `useNoteEditorStore.saveIfDirty`.
- Mode, work-mode, and model switchers in the header, using three new WS
  handlers that mirror the TUI's `toggle_mode` / `toggle_work_mode` /
  `cycle_model` semantics (including the "reject if run active" rule).
- An indicator area that shows the most recent `progress_event` (kind
  `status` / `error` / `sleep_*`) and the selected model / mode — the browser
  analogue of `src/aunic/tui/app.py:1453` `_indicator_fragments`.
- First-pass `permission_request` UI. The backend already emits
  `permission_request` on every prompted tool call and the run blocks until
  `resolve_permission` comes back; without a UI the first tool call in any run
  would hang the browser.
- `run_active` hydration on reconnect is already delivered by the existing
  `hello` → `session_state` flow (`src/aunic/browser/connection.py:109-118`);
  Plan 6 just has to bind the UI to it.

---

## Scope

### In scope
1. Three new WS request types and handlers: `set_mode`, `set_work_mode`,
   `select_model`. All reject during an active run, all rebroadcast
   `session_state`.
2. A new frontend `PromptComposer` mounted in `web/src/App.tsx` between the
   note editor and the transcript, containing: mode pill, work-mode pill,
   model dropdown, prompt editor, indicator line, Send/Cancel buttons.
3. A new `PromptEditor` React component that uses the existing
   `CodeMirrorHost` with a *different* extension set than the note editor
   (no markdown parser, no active-line raw markdown, no managed-section fold).
4. Three new CM6 extensions under `web/src/components/editor/extensions/`:
   `promptSyntax.ts` (classes for `/cmd`, `@cmd`, edit markers), and
   `promptAutocomplete.ts` (static vocabulary), `promptKeymap.ts` (Ctrl/Cmd+Enter
   send, Esc cancel, mobile-safe fallthrough). Reuse the existing
   `editCommandMarkers.ts`, `softWrapIndent.ts`, and `aunicTheme.ts`.
5. A zustand `prompt` slice for draft text + submit/cancel state, and an
   extended `session` slice that exposes `runActive`, `currentRunId`,
   `pendingPermission`, and a derived `indicatorMessage` fed by
   `progress_event`.
6. A `PermissionPrompt` banner rendered above the composer whenever
   `pending_permission` is non-null, with Once / Always / Reject buttons
   wired to the existing `resolve_permission` request.
7. Save-on-send: before `submit_prompt`, call
   `useNoteEditorStore.getState().saveIfDirty(client, currentDoc)` and abort
   the submission if it returns false.
8. Unit tests for the prompt slice, session-slice indicator derivation, and
   the composer's send flow. Backend pytest cases for the three new handlers.

### Deferred (explicit non-goals)
- **Dynamic `@rag` / `@<scope>` completion.** Scopes register at runtime
  (`src/aunic/tui/rendering.py:61`); v1 uses only the static set
  (`@web`, `@rag`, `/context`, `/note`, `/chat`, `/work`, `/read`, `/off`,
  `/model`, `/find`, `/replace`, `/include`, `/exclude`, `/isolate`, `/map`,
  plus the five edit-marker pairs from `src/aunic/context/markers.py:19-24`).
- **Prompt markdown rendering** — overview is explicit: "do not render tables,
  headers, bold, or italic syntax" in the prompt editor.
- **`@docs`** — listed in the overview but not implemented in the backend
  (confirmed via `src/aunic/tui/rendering.py`). Skip until it exists.
- **Settings / gear icon.** Still a placeholder per overview.
- **Sort/reorder model list, favorite models, multi-profile.** Pick the one
  the user had selected in the CLI; list order matches backend order.

---

## Default policy

Defaults I've picked; flip any in review before I start:

| # | Decision | Default | Rationale |
|---|---|---|---|
| 1 | Desktop send key | `Ctrl/Cmd+Enter` | Overview §Prompt Editor says "Enter inserts newline; shortcut sends". TUI uses Ctrl+R but that's a terminal-ism. |
| 2 | Mobile send | Always-visible Send button in composer footer | Overview: "mobile must always have an obvious send button". |
| 3 | Cancel during run | Esc while composer focused + visible Cancel button replacing Send | Mirrors TUI Esc/Ctrl+C (`src/aunic/tui/app.py:411-414`). |
| 4 | Composer height | Grows to `max(30vh, 16rem)` then scrolls internally | Overview §Prompt Editor. |
| 5 | Mode switcher UI | Two-pill segmented control labelled `Note` / `Chat` | Compact; readable on mobile. |
| 6 | Work-mode switcher UI | Three-pill segmented control `Off` / `Read` / `Work` | Matches TUI cycle but is tap-friendly. |
| 7 | Model picker UI | `<select>`-based dropdown with label showing current model | Simple; a combobox can be added later. |
| 8 | Permission UI | Inline banner above composer (not a modal) | Mobile-safe; doesn't steal focus mid-typing. |
| 9 | Indicator scope | Single-line most-recent message; hides `file_written` (noisy) and `tool_call` (already visible in transcript) | Keeps it legible. |
| 10 | Autocomplete trigger | Explicit (`Ctrl+Space`) + implicit on `/` and `@` at line/word start | CM6 default + cheap to wire. |
| 11 | Send-while-dirty | Save first, block submit on save failure, surface the save error | Per overview "Save-on-send". |
| 12 | Mode / model change during run | WS handler rejects with `run_active`, UI disables the control | Mirrors TUI guard in `controller.py:1005-1031`. |

---

## Backend changes

### `src/aunic/browser/messages.py`
Extend `CLIENT_MESSAGE_TYPES` (`messages.py:19-34`) with:
```python
"set_mode", "set_work_mode", "select_model",
```

### `src/aunic/browser/session.py`
Three new methods, each fire-and-forgot broadcasts `session_state` on success
and raises `BrowserError("run_active")` if `self.run_active`. Mirror the
TUI guards in `src/aunic/tui/controller.py:1005-1031`.

```python
async def set_mode(self, mode: Literal["note", "chat"]) -> None: ...
async def set_work_mode(self, work_mode: str) -> None: ...      # "off" | "read" | "work"
async def select_model(self, index: int) -> None: ...           # validate 0 <= i < len(model_options)
```

All three validate inputs and call the existing `broadcast_session_state()`
helper (the same one used by `submit_prompt` at `session.py:328`).

### `src/aunic/browser/connection.py`
Three new branches alongside the existing `submit_prompt` branch at line 197.
Each reads its payload, calls the session method, returns a one-line response.
Revision / path concerns don't apply — these are session-scoped, not file-scoped.

### Tests — `tests/test_browser_session.py`
Add cases for each new method:
- happy path (state updates + `session_state` broadcast observed)
- rejection when `run_active`
- rejection on invalid input (bad mode, bad work_mode, out-of-range index)

---

## Frontend changes

### Types — `web/src/ws/types.ts`
Extend `ClientMessageType` union with `"set_mode" | "set_work_mode" | "select_model"`.
Narrow `ProgressEventPayload.kind` to the known set `"status" | "error" | "sleep_started" | "sleep_ended" | "file_written" | "tool_call" | "tool_result" | "tool_error" | string` so the indicator store can discriminate.

### Session slice — `web/src/state/session.ts`
Already holds `SessionStatePayload`; add derived getters / selectors and a
`indicatorMessage: { text: string; kind: string; at: Date } | null` slot fed by
a new `applyProgressEvent` action. Indicator filter rules: ignore
`file_written` and `tool_call`; everything else wins.

### New prompt slice — `web/src/state/prompt.ts`
```ts
interface PromptSlice {
  draft: string;
  submitting: boolean;
  error: string | null;
  lastSubmittedAt: Date | null;
  setDraft(text: string): void;
  submit(client, activeFile, includedFiles): Promise<void>;  // saves first, then submit_prompt
  cancel(client, runId): Promise<void>;
  clear(): void;
}
```

### WS wiring — `web/src/ws/context.tsx`
Add three subscriptions alongside the existing transcript/file handlers:
- `progress_event` → `useSessionStore.getState().applyProgressEvent(event)`
- `session_state` (already wired to `setSession`) — also relay
  `pending_permission` to the session slice
- `permission_request` (one-shot, id-correlated) — store into session slice

### New components under `web/src/components/prompt/`
- `PromptComposer.tsx` — layout container; renders header row (mode pills,
  work-mode pills, model dropdown) + permission banner (when present) +
  `PromptEditor` + indicator line + footer row (Send/Cancel).
- `PromptEditor.tsx` — thin wrapper around `CodeMirrorHost` with the
  prompt-only extension set.
- `IndicatorLine.tsx` — single-line status; selectable; copy on click is native.
- `ModeSwitcher.tsx`, `WorkModeSwitcher.tsx`, `ModelPicker.tsx` — three small
  controls; disabled during active run.
- `SendCancelControls.tsx` — shows `Send` when idle, `Cancel` while
  `runActive`; obeys `submitting` / `saving`.
- `PermissionPrompt.tsx` — banner with message, target, three action buttons.

### New editor extensions under `web/src/components/editor/extensions/`
- `promptSyntax.ts` — a tiny `ViewPlugin` + `Decoration` pass over the doc:
  class `cm-prompt-slash` for `\s/\w+`, `cm-prompt-at` for `\s@\w+`,
  `cm-prompt-marker` for the five edit-marker openers/closers. No parser —
  single regex walk per visible range.
- `promptAutocomplete.ts` — CM6 `autocompletion()` with a static source that
  switches on the trigger char at the cursor. Seeded from the static vocab
  listed above.
- `promptKeymap.ts` — `Ctrl-Enter` / `Cmd-Enter` → calls the composer's submit
  callback via a state field that the React parent writes into. Esc while
  `runActive` → cancel.

### Reused editor extensions
- `softWrapIndent.ts`, `aunicTheme.ts`, `editCommandMarkers.ts` —
  already exist; import as-is.
- **Excluded:** `activeLineRawMarkdown.ts`, `managedSectionAutoFold.ts`, the
  markdown language pack, line numbers, fold gutter.

### App mount — `web/src/App.tsx`
Insert `<PromptComposer />` immediately after `<NoteEditor />`
(`App.tsx:33-34`). It renders `null` when no file is open. The existing
`workspace-main--transcript-maximized` logic already hides the note editor
when the transcript is maximized; do the same for the composer.

### Styles — `web/src/index.css`
Add `.prompt-composer`, `.prompt-composer__header`, `.prompt-composer__footer`,
`.prompt-indicator`, `.prompt-indicator--error`, `.mode-pill`,
`.mode-pill--active`, `.model-picker`, `.permission-prompt`,
`.permission-prompt__actions`. Inherit the existing `.panel` shell.

### Tests
- `web/src/state/prompt.test.ts` — submit() saves first, aborts on save
  failure, surfaces WS errors, clears draft on success.
- `web/src/state/session.test.ts` — (new file) indicator derivation rules.
- `web/src/components/prompt/PromptComposer.test.tsx` — renders null when no
  file open, disables mode/model during run, shows Cancel during run,
  renders permission banner, Ctrl+Enter submits.

---

## Critical files

### Modify (backend)
- `src/aunic/browser/messages.py` — add three message types to the allow-list.
- `src/aunic/browser/connection.py` — route the three new types.
- `src/aunic/browser/session.py` — implement three new methods; reuse
  existing `broadcast_session_state()` helper.
- `tests/test_browser_session.py` — add cases.

### Modify (frontend)
- `web/src/App.tsx` — mount composer.
- `web/src/ws/types.ts` — extend client message union, narrow progress kind.
- `web/src/ws/context.tsx` — subscribe to `progress_event`; relay
  `permission_request` and `pending_permission`.
- `web/src/state/session.ts` — indicator derivation, permission relay.
- `web/src/index.css` — composer + picker + indicator + banner styles.

### Add (frontend)
- `web/src/state/prompt.ts`
- `web/src/state/prompt.test.ts`
- `web/src/state/session.test.ts`
- `web/src/components/prompt/PromptComposer.tsx`
- `web/src/components/prompt/PromptEditor.tsx`
- `web/src/components/prompt/IndicatorLine.tsx`
- `web/src/components/prompt/ModeSwitcher.tsx`
- `web/src/components/prompt/WorkModeSwitcher.tsx`
- `web/src/components/prompt/ModelPicker.tsx`
- `web/src/components/prompt/SendCancelControls.tsx`
- `web/src/components/prompt/PermissionPrompt.tsx`
- `web/src/components/prompt/PromptComposer.test.tsx`
- `web/src/components/editor/extensions/promptSyntax.ts`
- `web/src/components/editor/extensions/promptAutocomplete.ts`
- `web/src/components/editor/extensions/promptKeymap.ts`

### Reference only (read, do not modify)
- `src/aunic/tui/controller.py:824` (`send_prompt`), `:1005-1031`
  (mode/work/model toggles), `:1453` (indicator fragments).
- `src/aunic/tui/rendering.py:30-61` (prompt vocabulary).
- `src/aunic/context/markers.py:19-24` (edit-marker pairs).
- `web/src/components/editor/CodeMirrorHost.tsx` (generic editor host).
- `web/src/components/editor/NoteEditor.tsx` (extension pattern, not logic).
- `src/aunic/browser/session.py:298-370` (existing submit/cancel/permission).

---

## Verification

### Automated
```bash
# Python
pytest tests/test_browser_session.py

# Web
cd web && npm run test
cd web && npm run build   # includes tsc type-check
cd web && npm run lint
```

### Manual (14 checks)

Start backend + dev server, open a markdown file in the browser.

1. Prompt editor renders; typing works; `Enter` inserts a newline.
2. `Ctrl+Enter` (or `Cmd+Enter`) submits. During submit: Send → "Saving…" →
   "Submitting…" → Cancel button.
3. Dirty file: the saved snapshot appears in the note editor *before* the
   run starts (watch the editor notice update).
4. Save failure case: force a revision conflict by bumping the file on disk,
   type, hit send — submission aborts with the note editor's conflict flow.
5. Transcript shows the user prompt row immediately, then assistant rows as
   the model replies (this already works; just confirm wiring didn't break).
6. Indicator area shows the latest `progress_event` message and updates live.
7. Error event (e.g. bash failure) flips the indicator to the error style.
8. Switch mode: Note ↔ Chat updates immediately, backend `session_state` echo.
9. Switch work-mode: Off → Read → Work cycles and persists in
   `session_state`.
10. Switch model from the dropdown; `selected_model` updates in the
    session_state payload.
11. During an active run, mode / work-mode / model controls are disabled;
    clicking them does nothing and the WS would reject anyway.
12. Cancel button ends the run: Cancel → Send, `run_active=false`.
13. Permission banner appears on a tool call that requires it; Once / Always /
    Reject each resolve correctly and the run continues or stops.
14. Reconnect (kill the server, restart, reload): on `hello` the UI restores
    `run_active`, selected model, mode, work-mode, and any pending permission
    banner from the fresh `session_state` without any manual refresh.

---

## Done criteria

- [ ] All 14 manual checks pass.
- [ ] `pytest tests/test_browser_session.py` green.
- [ ] `npm run test`, `npm run build`, `npm run lint` all green.
- [ ] No new console warnings in the browser during the manual run-through.
- [ ] `CLIENT_MESSAGE_TYPES` in `messages.py` and `ClientMessageType` in
      `types.ts` agree.
- [ ] Plan 6 line in `notes-and-plans/UI/browser-ui/browserUI-overview.md`
      updated to `[status: finished]` (and Plan 5's while we're there).
