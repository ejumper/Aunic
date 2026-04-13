# Plan: /include Feature

## Context

Users want to organize information across multiple note files while still having all content sent to the model in a single run. The `/include` command lets a note "pull in" other files (or entire directories) so that their note-body is concatenated with the active file's content before every model run. The transcript remains per-file; only the note-body portion is merged. This is conceptually straightforward because the multi-file context pipeline (`available_files` → `ContextEngine`) already exists — we just need to make it dynamically configurable at runtime and persisted.

## What Already Exists (Don't Reimplement)

- `TuiState.available_files: tuple[Path, ...]` — drives all multi-file reads (controller.py:127)
- `TuiState.included_files` property — derives non-active files from `available_files` (types.py:96)
- `ContextEngine.build_context()` + `FileManager.read_working_set()` — already reads and concatenates all files, labels each with its display path
- `_load_tui_file_state` / `_save_tui_file_state` in `app.py:1718-1740` — per-file JSON persistence in `~/.aunic/tui_prefs.json`
- `_PROMPT_COMMAND_RE` / `PROMPT_ACTIVE_COMMANDS` in `rendering.py:17-22` — slash command detection and prompt highlighting
- `send_prompt()` in `controller.py:655` — command dispatch switch
- File menu dialog (`_build_file_menu_dialog`, `_file_radio`, `_sync_file_radio`) in `app.py:1433-1509` — existing popup for switching files, triggered by clicking on the file name in the status bar. This is the UI to extend for the include list.

## Data Model

Add `IncludeEntry` and store the list in `tui_prefs.json` under each file's `file_state` entry.

```python
# src/aunic/tui/types.py
@dataclass
class IncludeEntry:
    path: str       # as stored (relative or absolute, as the user typed)
    is_dir: bool    # True → directory include
    recursive: bool # True → recursive glob (only meaningful when is_dir=True)
    active: bool = True
```

Persisted in `tui_prefs.json`:
```json
{
  "file_state": {
    "/abs/path/to/note.md": {
      "transcript_open": true,
      "includes": [
        {"path": "./research/", "is_dir": true, "recursive": false, "active": true},
        {"path": "./context.md", "is_dir": false, "recursive": false, "active": false}
      ]
    }
  }
}
```

`TuiState` gains one new field:
```python
include_entries: list[IncludeEntry] = field(default_factory=list)
```

`available_files` continues to be the runtime source of truth. It is rebuilt whenever `include_entries` changes.

## Implementation Steps

### 1. `types.py` — Add `IncludeEntry`, add `include_entries` to `TuiState`

- Add `IncludeEntry` dataclass (no deps, just a plain dataclass)
- Add `include_entries: list[IncludeEntry] = field(default_factory=list)` to `TuiState`

### 2. `controller.py` — `_rebuild_available_files()` and command handlers

**Add `_rebuild_available_files()`** — resolves `include_entries` to concrete paths, rebuilds `available_files`:
- For each active `IncludeEntry`:
  - `is_dir=False`: resolve `entry.path` relative to `active_file.parent`, use as-is
  - `is_dir=True, recursive=False`: `glob("*.md")` on the resolved directory
  - `is_dir=True, recursive=True`: `rglob("*.md")` on the resolved directory
  - Skip paths that don't exist (silent)
- Set `self.state.available_files = (active_file, *deduplicated_resolved_paths)`
- Call `self._on_includes_changed()` if the callback is set

Add `_on_includes_changed: Callable[[], None] | None = None` field (same pattern as `_on_transcript_open_changed`).

Call `_rebuild_available_files()` at:
- Startup (after loading include_entries from prefs, in controller `__init__` or from app.py init)
- After any `/include`, `/exclude`, or toggle-active/remove action
- On file switch (each file has its own include list)

**Add `/include` handler in `send_prompt()`:**
- Parse `remaining` for optional `-r` flag, then the path argument
- Detect `is_dir`: path ends with `/` OR resolves to an existing directory
- Append `IncludeEntry` if not already present (match on `path` string)
- Call `_rebuild_available_files()`
- Set status message, clear prompt

**Add `/exclude` handler** — remove the `IncludeEntry` whose `path` matches the argument, then `_rebuild_available_files()`.

**Add `/isolate` handler** — parse remaining text:
- `/isolate <prompt>` → override for this run to only use the active file
- `/isolate /path/one /path/two <prompt>` → override to only use listed paths + prompt is the non-path text

Store as `_isolate_override: tuple[Path, ...] | None = None` on the controller (not persisted). In `_run_current_mode()`, use `_isolate_override` if set instead of `state.included_files`, then set it to `None` after the run completes (in the `finally` block or equivalent cleanup).

Parsing heuristic for `/isolate`: collect leading tokens that look like paths (start with `/`, `./`, or `../`); the rest is the prompt text.

**Add `toggle_include_active(index: int)` and `remove_include(index: int)` methods** on the controller — called from the file menu UI buttons. Both call `_rebuild_available_files()` after mutating `include_entries`.

### 3. `app.py` — Persist/load include list; extend file menu dialog

**Init (around line 95):**
```python
raw_includes = file_ui_state.get("includes")
if isinstance(raw_includes, list):
    self.controller.state.include_entries = _deserialize_include_entries(raw_includes)
    self.controller._rebuild_available_files()
```

**Persist on change:**
```python
def _save_includes() -> None:
    _save_tui_file_state(active_file, {
        "transcript_open": ...,
        "transcript_maximized": ...,
        "includes": _serialize_include_entries(self.controller.state.include_entries),
    })
self.controller._on_includes_changed = _save_includes
```

Note: save the full file_state dict each time (all keys together), not just "includes" — same pattern as the existing transcript state save.

**Extend `_build_file_menu_dialog()`** — change the dialog body from a plain `RadioList` to a custom `FormattedTextControl` (or keep the `RadioList` for the active file and add a section below it for included files with inline [X]/[*] buttons). The simplest approach:

- Keep `RadioList` for all `available_files` (existing behavior — clicking opens the file)
- Below the RadioList, render a separate list of include entries with `[X]` (remove) and `[*]`/`[ ]` (toggle active) buttons
- `[X]` calls `controller.remove_include(index)` + re-syncs the dialog
- `[*]` calls `controller.toggle_include_active(index)` + re-syncs the dialog

**Filename disambiguation**: When building display labels, if two entries share the same `Path.name`, progressively include more parent path components until all labels are unique.

**`_sync_file_radio()`**: update to use disambiguated labels when there are name collisions.

Add helper functions `_serialize_include_entries` and `_deserialize_include_entries` near `_load_tui_file_state`.

### 4. `rendering.py` — Register new commands

Add to `PROMPT_ACTIVE_COMMANDS`:
```python
"/include", "/exclude", "/isolate"
```

Extend `_PROMPT_COMMAND_RE`:
```
/include\b|/exclude\b|/isolate\b
```

### 5. On file switch — load per-file includes

When `request_file_switch()` / `confirm_file_switch()` runs (controller.py), after switching `active_file`, reload `include_entries` from prefs for the new file and call `_rebuild_available_files()`. The app.py init pattern should be extracted into a reusable `_load_file_ui_state(path)` method callable at switch time.

## Files to Modify

| File | Change |
|------|--------|
| `src/aunic/tui/types.py` | Add `IncludeEntry` dataclass; add `include_entries` field to `TuiState` |
| `src/aunic/tui/controller.py` | Add `_rebuild_available_files()`, `/include`/`/exclude`/`/isolate` handlers, `toggle_include_active()`, `remove_include()`, `_on_includes_changed` callback, `_isolate_override` field |
| `src/aunic/tui/app.py` | Load/save `include_entries`; extend file menu dialog with [X]/[*] controls; handle includes on file switch; serialize/deserialize helpers |
| `src/aunic/tui/rendering.py` | Add `/include`, `/exclude`, `/isolate` to `PROMPT_ACTIVE_COMMANDS` and `_PROMPT_COMMAND_RE` |

No changes needed to `context/engine.py`, `context/file_manager.py`, `modes/runner.py`, or any provider code — they already handle multiple files correctly.

## Verification

1. `/include ./other.md` → status confirms; file menu dialog now lists both files; model run sends both files' note-content.
2. Restart TUI → includes persist (reloaded from `tui_prefs.json`).
3. `/exclude ./other.md` → entry removed; next run only sends active file.
4. `/include ./research/` → all `.md` files in that dir included (non-recursive). Add a new `.md` file to that dir, run again → new file is included.
5. `/include -r ./research/` → all `.md` files recursively included.
6. `/isolate <prompt>` → that one run uses only active file; next run uses full includes again.
7. `/isolate ./a.md ./b.md <prompt>` → that one run uses only listed files + active.
8. Two included files with the same name → labels disambiguated in file menu.
9. `[X]` button removes entry; `[*]` toggles active (dimmed in list, skipped on run).
10. Clicking a file name in the dialog saves + closes current file and opens the selected one (existing behavior, unchanged).
11. Switch to a different note → its own separate include list loads.
12. `pytest tests/ -x -q` — all existing tests pass.
