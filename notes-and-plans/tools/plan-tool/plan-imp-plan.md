# Plan Tool — Implementation Plan for Aunic

## Context

Aunic's thesis treats the markdown note as the primary context and chat as a secondary tool for *building* that context. The example coding agent (`/home/ejumps/HalfaCloud/Backups/coding-agent-program-example`) implements a "Plan" tool that already aligns well with this thesis: it treats planning as a workflow state with a durable markdown file as the source of truth, and approval as a hard boundary before mutation.

We want Aunic to gain that same capability, but adapted to Aunic's note-first shape rather than the example's chat-first shape:

- Plans must be **attached to a source note** (the Aunic markdown file the user is working in), stored under that note's sibling `.aunic/plans/` directory, and visible in the file menu.
- Planning is a **mode**, not just a document — while planning, the model may explore and update the plan file but cannot mutate project files or the source note.
- The plan file is the **source of truth**. `exit_plan` reads from disk at approval time so the user can edit the plan directly before approving.
- A plan file open in the editor must **not become the model's context source** — context is still built from the source note. Display and context must be split.
- Plan tools must be **ephemeral** (mirroring `note_edit`/`note_write`) so the durable plan file is the record, not bloated transcript rows.

`notes-and-plans/tools/plan-tool/plan-tool.md` is the design research; this file is the executable plan that translates it into Aunic.

## Gold-Standard Reference

The example project's plan tool is the gold standard. We follow it where Aunic's architecture allows and diverge only where note-first design requires it.

| Example concept | Aunic adaptation |
|---|---|
| `EnterPlanMode` deferred tool | `/plan` slash command + `enter_plan_mode` tool — entry stays user-driven; tool exists for model-initiated planning |
| Single global plan dir keyed by session slug | Per-source-note `.aunic/plans/` directory keyed by plan-title slug |
| `getPlan()` reads the session's plan file | `RunToolContext.read_active_plan()` reads `state.active_plan_path` |
| `ExitPlanMode` reads plan from disk, presents approval UI | `exit_plan` tool reads from disk, surfaces approval via existing `permission_prompt` modal pattern |
| `prePlanMode` saved on `toolPermissionContext` | `pre_plan_work_mode` saved on `TuiState`, restored on exit |
| `prepareContextForPlanMode()` strips dangerous perms | `_apply_plan_mode_tool_filter()` strips mutating tools from registry while planning |
| `planModeV2.ts` system reminders | New `PLAN_MODE_SYSTEM_PROMPT` block injected by `_build_system_prompt()` |
| `/plan` command (enter / show / open) | `/plan [title]`, `/plan list`, `/plan open` |
| `plan_file_reference` attachment for compaction recovery | Plan frontmatter + `.aunic/plans/index.json` (recovery is file-based, no transcript snapshot needed) |

We **do not** copy:
- Global plan directory (Aunic plans live with the note).
- Random word slugs (Aunic uses title-derived slugs as the user requested).
- Subagent-keyed plan files (Aunic doesn't have subagents).
- Teammate/leader approval mailbox.
- File-snapshot recovery flow (Aunic plans are durable on local disk).

## Storage Model

A source note at `/path/to/project/task.md` stores its plans under `/path/to/project/.aunic/plans/`:

```
/path/to/project/
  task.md
  .aunic/
    task.meta.json                 # existing per-note metadata
    plans/
      index.json                   # plan registry for this dir's notes
      migrate-task-runner.md       # plan file (slug from top heading)
      add-import-preview-2.md      # collision suffix
```

This mirrors the existing `meta_path_for(note_path)` convention at [src/aunic/map/manifest.py:21-23](src/aunic/map/manifest.py#L21-L23) — same `.aunic/` parent directory, same per-directory grouping. `index.json` is shared across all notes in that directory; each entry records its `source_note` so the UI can filter to plans for the active note.

### Plan file format

```markdown
---
aunic_type: plan
plan_id: 2026-04-16-migrate-task-runner
source_note: ../task.md
status: draft               # draft | awaiting_approval | approved | implementing | implemented | archived | rejected
created_at: 2026-04-16T10:30:00-05:00
updated_at: 2026-04-16T10:44:00-05:00
---

# Migrate Task Runner To Persistent Queue

...
```

### Index format (`.aunic/plans/index.json`)

```json
{
  "version": 1,
  "plans": [
    {
      "id": "2026-04-16-migrate-task-runner",
      "source_note": "../task.md",
      "path": "migrate-task-runner.md",
      "title": "Migrate Task Runner To Persistent Queue",
      "status": "draft",
      "created_at": "2026-04-16T10:30:00-05:00",
      "updated_at": "2026-04-16T10:44:00-05:00",
      "approved_at": null,
      "implemented_at": null
    }
  ]
}
```

The frontmatter is the durable per-plan record (so plans survive being moved); the index is a fast lookup the UI/file-menu can read without scanning every plan.

### Slug & rename rules

- Slug = kebab-case of the first `# H1` heading (lowercase, ASCII-fold, replace non-alnum with `-`, collapse repeats, strip edges).
- Collision: append `-2`, `-3`, … in order. (Reserve short hashes for imported/recovered plans only.)
- Rename: only on explicit save or on `exit_plan` approval, never on every keystroke. If the new slug collides, keep the old filename and only update title metadata.

## Source-of-Truth Split: `display_file` vs `context_file`

The most invasive change. Today [src/aunic/tui/types.py:77](src/aunic/tui/types.py#L77) has `active_file: Path` and that field is overloaded as: (a) what the editor shows, (b) what context is built from, (c) what note tools target.

We split it into:

```python
# tui/types.py — TuiState
context_file: Path                    # source note — context is built from this
display_file: Path                    # file shown in the editor (defaults to context_file)
active_plan_id: str | None = None     # selected plan, if any
planning_status: PlanningStatus = "none"  # none | drafting | awaiting_approval | approved | implementing
pre_plan_work_mode: WorkMode | None = None  # for restoring on exit, mirrors example's prePlanMode
```

`active_file` becomes a `@property` that returns `context_file` for the transition period; new code reads `context_file` / `display_file` directly. This keeps the diff bounded and lets us migrate call sites incrementally.

**Behavioral rules**:
- Context building, prompt assembly, and `note_edit`/`note_write` always operate on `context_file`.
- Editor save writes whatever `display_file` points at. When `display_file == active_plan_path`, the save target is the plan file.
- Selecting an included file changes both `context_file` and `display_file` (current behavior).
- Selecting a plan changes only `display_file` and sets `active_plan_id`. `context_file` stays put.

## Tool Set

New file [src/aunic/tools/plan.py](src/aunic/tools/plan.py) — mirrors [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py) structure (parse → execute → ephemeral `ToolExecutionResult`).

| Tool | Persistence | Purpose |
|---|---|---|
| `enter_plan_mode` | persistent | Model-initiated entry into planning state. No-op if already planning. Mirrors example's `EnterPlanMode`. |
| `plan_create` | persistent | Create a new plan file under the source note's `.aunic/plans/`. Sets `active_plan_id`, switches `planning_status` to `drafting`, opens plan in editor. |
| `plan_write` | ephemeral | Replace the active plan file's full content. Mirrors `note_write` exactly (conflict detection via live read). |
| `plan_edit` | ephemeral | Exact `old_string`/`new_string` replacement on the active plan file. Mirrors `note_edit` exactly. |
| `exit_plan` | persistent | **Reads plan from disk** (never from input — critical), surfaces approval UI, records lifecycle row, restores `pre_plan_work_mode` on approve. |

Lifecycle tools (`plan_list`, `plan_open`, `plan_archive`, `plan_status`) are **deferred to Phase 3** — the user can do these via `/plan` slash subcommands and the file menu in Phase 1/2.

### Persistent vs ephemeral choice

`plan_write`/`plan_edit` are ephemeral (like `note_edit`/`note_write`) because the plan file is the durable record — no need to spam the transcript with full plan dumps after every edit. `enter_plan_mode`, `plan_create`, and `exit_plan` are **persistent** because they are lifecycle events worth keeping in the transcript record.

### `exit_plan` approval flow

1. Validate `planning_status in {drafting, awaiting_approval}`.
2. Read plan markdown from `state.active_plan_path` on disk (NOT from tool input — preserves the example's best design choice).
3. Set `planning_status = "awaiting_approval"`, set `state.permission_prompt = PlanApprovalPromptState(...)` reusing the existing modal infrastructure at [src/aunic/tui/types.py:18-23](src/aunic/tui/types.py#L18-L23).
4. Modal exposes one primary action and a dismiss path (deliberately minimal — Aunic's editor and prompt box already cover edit/reject ergonomics):
   - **Approve & implement**: set `planning_status = "approved"`, set `work_mode = "work"`, return success result with the plan body inlined for the model. The model's next turn enters implementation with the full plan as a `tool_result` payload.
   - **Dismiss / Keep planning**: tool returns a `tool_error` with `category="user_cancel"`, `planning_status` stays `drafting`, `work_mode` is left unchanged. The user can keep editing the plan in the editor or send a follow-up prompt with revision feedback.
5. After approval, write a compact lifecycle row to the source note transcript (not the plan body — just the plan path + approval timestamp + status).
6. `pre_plan_work_mode` is still recorded on entry so a future "Approve only" action can restore it verbatim without another refactor; Milestone 1 simply doesn't expose that button.

## Planning-State Permissions

While `planning_status in {drafting, awaiting_approval}`:

- Plan tools (`plan_write`, `plan_edit`, `exit_plan`) are allowed.
- Read-only tools (`read`, `grep`, `glob`, `list`, `read_map`, `web_search`, `web_fetch`, RAG, memory) are allowed.
- `note_edit`, `note_write`, `edit`, `write`, `bash` are **stripped from the tool registry** by a new filter in [src/aunic/loop/runner.py:559-580](src/aunic/loop/runner.py#L559-L580), parallel to `_apply_marker_tool_filter`.
- The system prompt explicitly says: "You are planning. The only mutable target is the plan file. Use plan_edit/plan_write to update it. When ready, call exit_plan."

`work_mode` itself is preserved (we just filter the registry), so on `exit_plan` approval we restore the previous registry by clearing `planning_status`. The `pre_plan_work_mode` field exists for the case where the user wants approval to *escalate* `work_mode` (e.g., they were in `read` mode while planning but want `work` for implementation).

## Context Assembly Changes

[src/aunic/tools/runtime.py:208-221](src/aunic/tools/runtime.py#L208-L221) — `RunToolContext.note_snapshot_text()`:

- Always builds from `context_file` (already does, since `active_file` ≡ `context_file` post-refactor).
- When `planning_status in {drafting, awaiting_approval}` and an active plan exists, append a **`PLAN DRAFT` snapshot block** after `READ-ONLY MAP`, clearly labeled as the current planning artifact (not source-note context):

```
PLAN DRAFT
PLAN FILE: /path/to/.aunic/plans/migrate-task-runner.md
STATUS: drafting

# Migrate Task Runner To Persistent Queue
...
```

- When a plan is merely *open in the editor* but planning is not active, do NOT inject the plan body. The user notes are explicit on this:
  > If a prompt is sent with the plan file open in the editor, the context should be sent as if the actual aunic file was open.

[src/aunic/loop/runner.py:583-617](src/aunic/loop/runner.py#L583-L617) — `_build_system_prompt()`:

- Add a `planning_status` parameter.
- When planning, prepend `PLAN_MODE_SYSTEM_PROMPT` (interview-style: skeletal plan → explore → update → ask only what code can't answer → request approval).
- Include a `PLAN APPROVED — implement now` reminder when `planning_status == "approved"` and the loop is starting an implementation turn.

## UI Changes

### File menu (`tui/app.py`)

Add a "Plans" section below the existing "Included" section in the file-menu dialog. Source: `PlanService.list_plans_for_source_note(context_file)`.

```
task.md
  Included
    architecture.md
  Plans
    [draft] Migrate Task Runner To Persistent Queue
    [approved] Add Import Preview
    + New Plan
```

Selecting a plan calls `controller.open_plan(plan_id)` → sets `display_file = plan_path`, sets `active_plan_id`, leaves `context_file` alone, persists to `tui_prefs.json` per the existing pattern at [src/aunic/tui/app.py:98-122](src/aunic/tui/app.py#L98-L122).

### Top-bar breadcrumb

When `display_file != context_file`, show: `Plan: <title> (source: <context_file.name>)` in `indicator_message`. The user must never wonder which document the editor is showing.

### Approval modal

Reuse `PermissionPromptState` at [src/aunic/tui/types.py:17-23](src/aunic/tui/types.py#L17-L23). Add a `PlanApprovalPromptState` variant (or extend `PermissionPromptState.details`) rendered through the existing `permission_prompt` dialog mode. Two buttons only:

- **Approve & implement** (primary) — sets `planning_status = "approved"`, `work_mode = "work"`, resumes the loop.
- **Keep planning** (dismiss / Esc) — leaves state in `drafting`, no `work_mode` change.

The plan body is shown rendered in the modal so the user can review it without leaving the dialog. To revise, the user dismisses the modal and edits the plan file directly in Aunic's editor — no separate "Edit plan" button needed.

### Slash commands

In [src/aunic/tui/controller.py:740-820](src/aunic/tui/controller.py#L740-L820), alongside `/work`, `/read`, `/off`, add:

| Command | Behavior |
|---|---|
| `/plan` | If no active plan and 0 drafts: enter planning, prompt model to draft. If 1 draft: open it. If >1 drafts: open file menu Plans section. If already planning: show current plan path. |
| `/plan <title>` | Create a new plan with that title and enter planning. |
| `/plan list` | Open file menu, scrolled to Plans section. |
| `/plan open` | Open the active plan file in the external `$EDITOR`. |

## File-Level Implementation

New files:
- [src/aunic/plans/__init__.py](src/aunic/plans/__init__.py)
- [src/aunic/plans/service.py](src/aunic/plans/service.py) — `PlanService` (slug, paths, index R/W, frontmatter parse/write, create/list/load/save/rename/archive/status)
- [src/aunic/plans/types.py](src/aunic/plans/types.py) — `PlanEntry`, `PlanStatus`, `PlanFrontmatter`, `PlanIndex`
- [src/aunic/tools/plan.py](src/aunic/tools/plan.py) — the five plan tools, mirroring `note_edit.py` shape
- [tests/test_plans_service.py](tests/test_plans_service.py)
- [tests/test_plan_tools.py](tests/test_plan_tools.py)
- [tests/test_planning_state.py](tests/test_planning_state.py)

Modified files:
- [src/aunic/tui/types.py](src/aunic/tui/types.py) — split `active_file` into `context_file`/`display_file`, add `active_plan_id`, `planning_status`, `pre_plan_work_mode`; add `PlanningStatus` literal; `active_file` becomes a property aliasing `context_file`.
- [src/aunic/tui/controller.py](src/aunic/tui/controller.py) — `/plan` command family, `open_plan`, `create_plan`, approval handlers, plan list refresh on file changes.
- [src/aunic/tui/app.py](src/aunic/tui/app.py) — Plans section in file menu, breadcrumb in top bar, approval modal rendering, plan-aware editor save dispatch, `tui_prefs.json` persistence of `active_plan_id`.
- [src/aunic/tools/runtime.py](src/aunic/tools/runtime.py) — add `active_plan_path: Path | None`, `planning_status`, `read_active_plan()`, `write_active_plan()`, `is_plan_scope_path()`. Extend `note_snapshot_text()` to append `PLAN DRAFT` block when planning.
- [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py) — `build_note_tool_registry`/`build_note_only_registry` accept `planning_status` and exclude mutating tools when planning.
- [src/aunic/loop/runner.py](src/aunic/loop/runner.py) — add `_apply_plan_mode_tool_filter()` parallel to `_apply_marker_tool_filter()`; pass `planning_status` to `_build_system_prompt()`; plumb `active_plan_path` and `planning_status` into `RunToolContext.create()`.
- [src/aunic/loop/system_prompts.py](src/aunic/loop/system_prompts.py) (or wherever `NOTE_LOOP_SYSTEM_PROMPT` lives) — add `PLAN_MODE_SYSTEM_PROMPT` constant.
- [src/aunic/transcript/writer.py](src/aunic/transcript/writer.py) — support a compact `plan_lifecycle` row type for created / approved / rejected / implemented events.
- [src/aunic/context/engine.py](src/aunic/context/engine.py) — verify it builds from `context_file` (it already uses `active_file` which becomes `context_file` post-rename); add a defensive guard that refuses to parse transcript rows from plan files.

## Milestone 1 — Single PR

Ship the whole feature as one cohesive change. Order of work inside the PR (each step compiles & tests on its own):

1. **`active_file` split**. Add `context_file` + `display_file` to `TuiState`; keep `active_file` as a `@property` aliasing `context_file`. Touch every site that reads `active_file` for context/tools to use `context_file`; sites that drive the editor use `display_file`. Verify existing test suite green.
2. **`plans/` package**. `PlanService`, types, frontmatter R/W, `index.json` R/W, slug + collision rules. Pure unit tests, no UI/loop coupling.
3. **TuiState planning fields**. `active_plan_id`, `planning_status`, `pre_plan_work_mode`, plan-related tui_prefs persistence.
4. **File menu Plans section + breadcrumb**. Selecting a plan sets `display_file` only. Manual smoke: create a plan file by hand, see it appear, open it, send a prompt — confirm context still comes from the source note.
5. **`RunToolContext` plan plumbing**. `active_plan_path`, `planning_status`, `read_active_plan()`, `write_active_plan()`. Extend `note_snapshot_text()` to append `PLAN DRAFT` block when planning.
6. **Plan tools**. `tools/plan.py` with `enter_plan_mode`, `plan_create`, `plan_write`, `plan_edit`, `exit_plan`. Mirror `note_edit.py` shape exactly.
7. **Loop integration**. `_apply_plan_mode_tool_filter` in `runner.py`. `PLAN_MODE_SYSTEM_PROMPT` injection.
8. **Approval modal**. Two-button `PlanApprovalPromptState`. Plumb the resolution back into `exit_plan`'s tool result.
9. **`/plan` slash commands**. `/plan`, `/plan <title>`, `/plan list`, `/plan open`.
10. **Lifecycle transcript rows**. Compact `plan_lifecycle` rows for created and approved.
11. **End-to-end tests** per the verification list below.

## Future Work (not in this PR)

- **Approve-only path**: surface a second modal button that restores `pre_plan_work_mode` verbatim instead of escalating to `work` (useful when the plan describes human-driven steps). The data path is already wired — only the button is missing.
- **Fresh implementation context**: an "Approve and clear" option that drops the planning transcript from the model's next turn and replaces it with an explicit "Implement the following plan" payload. Requires deciding how Aunic represents context resets generally.
- **Reentry intelligence**: `/plan` detecting existing drafts and offering resume-vs-new selection.
- **Plan archiving + status transitions**: `plan_archive`, `plan_status` tools and corresponding file-menu sections (`Archived`, `Implemented`).
- **External-editor open**: `/plan open` invoking `$EDITOR` if the user wants to edit a plan outside Aunic.

## Verification

End-to-end test scenarios for Milestone 1:

1. **Storage**: create a source note at `/tmp/p/task.md`, run `plan_create({title: "Foo Bar"})`, assert `/tmp/p/.aunic/plans/foo-bar.md` exists with correct frontmatter and `index.json` entry. Run again with same title, assert `foo-bar-2.md`.
2. **Display/context split**: open `task.md`, then open the plan from file menu. Assert `state.context_file == task.md`, `state.display_file == foo-bar.md`. Send a prompt with no `/plan` — assert the model receives `task.md`'s note snapshot, NOT the plan body.
3. **Planning state**: invoke `/plan`. Assert `planning_status == "drafting"` and the registry passed to the loop excludes `note_edit`, `note_write`, `edit`, `write`, `bash`. Assert `plan_edit`/`plan_write` are present.
4. **Disk-truth approval**: model calls `plan_write({...})`, then user manually edits the plan file in the editor and saves, then model calls `exit_plan`. Assert the approval modal shows the *user-edited* content (read from disk at approval time), not the model's last-written version.
5. **Dismiss path**: dismiss the approval modal. Assert `exit_plan` returns `tool_error` with `category="user_cancel"`, `planning_status` stays `drafting`, `work_mode` is unchanged, registry stays plan-scoped.
6. **Approval escalates work mode**: enter planning from `work_mode = "read"`, approve. Assert `work_mode = "work"`, `planning_status = "approved"`, the success result delivered to the model contains the full plan markdown.
7. **`pre_plan_work_mode` recorded**: regardless of the entry mode, assert `state.pre_plan_work_mode` matches the value at planning entry — verifies the data path the future "Approve only" button will use.
8. **Lifecycle rows**: assert source note transcript contains `plan_lifecycle` rows for `created`, `approved` — and that no row contains the full plan body (only path + status + timestamps).
9. **Index recovery**: delete `index.json`, reopen the note, assert the file menu rebuilds the plan list by scanning `.aunic/plans/*.md` frontmatter.
10. **Run the existing test suite** (`uv run pytest`) and confirm no regressions in `tests/test_note_edit_tools.py`, `tests/test_context_markers.py`, etc., from the `active_file` → `context_file` rename.

Manual smoke test:

- Open a real note in the TUI, type `/plan rewrite the importer`, watch the model draft a plan in the new plan pane, edit it yourself, then call `exit_plan`. Approve and watch implementation begin.

## Critical Files To Reference While Implementing

- [src/aunic/tools/note_edit.py](src/aunic/tools/note_edit.py) — exact pattern for `plan_write`/`plan_edit` (parse → execute → ephemeral result, conflict detection via live read).
- [src/aunic/map/manifest.py](src/aunic/map/manifest.py) — exact pattern for plan frontmatter/index R/W (per-note `.aunic/` JSON).
- [src/aunic/loop/runner.py:559-580](src/aunic/loop/runner.py#L559-L580) — `_apply_marker_tool_filter` is the model for `_apply_plan_mode_tool_filter`.
- [src/aunic/tools/runtime.py:111-174](src/aunic/tools/runtime.py#L111-L174) — `RunToolContext` is where `active_plan_path` and `planning_status` plumb in.
- [src/aunic/tui/controller.py:740-820](src/aunic/tui/controller.py#L740-L820) — slash command dispatch is where `/plan` lands.
- [src/aunic/tui/app.py:98-122](src/aunic/tui/app.py#L98-L122) and [src/aunic/tui/app.py:1773](src/aunic/tui/app.py#L1773) — `tui_prefs.json` per-file persistence pattern for `active_plan_id`.
- Example project, `src/utils/plans.ts` and `src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts` — keep open in a side buffer while implementing `exit_plan`; the disk-read-at-approval-time and pre-mode-restoration patterns are the parts most worth copying line-for-line.
