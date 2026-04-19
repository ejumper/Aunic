# Plan Tool
## User Notes
- plans created from any given "aunic" markdown file should be stored in the respective .aunic/ folder under .aunic/plans/, and named after the top header in the plan. They should be viewable in the UI by selecting the file name at the top and under the list of "included" files there should be a list of "plans" listing any plans associated with the open file.
    - selecting one of these files should open it in the editor.
        - note, plan files are not sent as context, they are handled the same way that /home/ejumps/HalfaCloud/Backups/coding-agent-program-example handles them. If a prompt is sent with the plan file open in the editor and a user-prompt is sent, the context is sent the same as if the actual aunic file was open.
## /home/ejumps/HalfaCloud/Backups/coding-agent-program-example, specifically its implementation of the "Plan" tool Implementation
The example project treats "Plan" as a workflow state with a durable markdown file, not as a single message or a blob of tool input. The important pieces are:

- `EnterPlanMode`: a tool that asks permission to enter a read-only planning state.
- Plan file management: utilities that create, locate, recover, copy, and read the current plan markdown file.
- Plan-mode instructions: system reminders that constrain the model to exploration, plan editing, and user questions.
- `ExitPlanMode`: a tool that requests user approval, reads the plan from disk, and transitions back to implementation mode.
- UI and slash-command support: `/plan`, approval dialogs, plan rendering, editing, rejection, and reentry behavior.

Relevant files in the example:

- `src/tools/EnterPlanModeTool/EnterPlanModeTool.ts`
- `src/tools/EnterPlanModeTool/prompt.ts`
- `src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts`
- `src/tools/ExitPlanModeTool/prompt.ts`
- `src/utils/plans.ts`
- `src/utils/messages.ts`
- `src/utils/planModeV2.ts`
- `src/commands/plan/plan.tsx`
- `src/components/permissions/EnterPlanModePermissionRequest/EnterPlanModePermissionRequest.tsx`
- `src/components/permissions/ExitPlanModePermissionRequest/ExitPlanModePermissionRequest.tsx`
- `src/bootstrap/state.ts`
- `src/Tool.ts`

### EnterPlanMode
The `EnterPlanMode` tool is a state transition tool. It has an empty input schema and does not create the plan content itself.

Important behavior:

- It is a deferred tool. The tool call asks the host application to enter plan mode, and the host resolves that transition through the permission system.
- It is marked read-only, because entering plan mode does not mutate project files.
- It can be rejected before execution if the current context cannot approve the transition, such as when called from a subagent.
- It stores the previous permission mode in `prePlanMode`, then switches `toolPermissionContext.mode` to `plan`.
- The tool result tells the model that it is now in plan mode and must use `ExitPlanMode` when the plan is ready.
- The user can also enter plan mode with `/plan`, which bypasses some of the normal prompt-permission ceremony because the user directly initiated the state change.

The tool prompt is careful about when plan mode is appropriate. It encourages plan mode for complex implementation work, ambiguous user requests, broad refactors, high-risk changes, and tasks where user confirmation would reduce wasted work. It discourages plan mode for simple questions, straightforward edits, or cases where the user explicitly asked the model to proceed.

That distinction matters: plan mode is not "think before acting" in the abstract. It is specifically a user-visible pause before mutation.

### The Plan File
The plan itself lives in a markdown file. The V2 implementation deliberately avoids passing the plan body as normal tool input to `ExitPlanMode`.

The core behavior lives in `src/utils/plans.ts`:

- The plan directory defaults to a global directory, usually under the agent config directory, unless project settings specify a custom `plansDirectory`.
- A session gets a stable generated plan filename, based on a random readable slug. The filename is cached per session.
- Subagents can get their own plan file paths, keyed by agent id.
- Plans can be copied during resume or fork flows so the new session has a usable plan file without mutating the old one.
- If the plan file is missing, the system can recover plan content from several places: prior tool input, transcript references, remote file snapshots, or attachments.
- The host can expose the plan file path to SDK hooks and logging by injecting `plan` and `planFilePath` locally, then stripping those fields before sending the tool call to the model provider.

The plan file is the source of truth. `ExitPlanMode` reads from the file at approval time. If the user edits the plan in the approval UI or external editor, the implementation syncs that edited text back to disk before treating it as approved.

This is a strong design choice. It means the user, model, host UI, and external editor are all looking at the same artifact. The plan is not hidden in a transcript turn, and it is not trapped inside an assistant message.

### Plan-Mode Instructions
The model receives special instructions while in plan mode. These are not just extra prose. They define a different operating contract.

The main rules are:

- The model may inspect the codebase and use read-only tools.
- The model may not implement the plan yet.
- The model may not edit arbitrary files.
- The only mutable file is the plan file.
- The model should keep the plan file updated as it learns more.
- The model should ask questions only when the answer cannot be discovered from available context.
- When the plan is ready, the model must call `ExitPlanMode`.

The example implementation includes several plan-mode reminder variants:

- A full planning workflow reminder.
- A sparse reminder for later turns.
- A reentry reminder when a session resumes or re-enters plan mode.
- An exit reminder after the plan is approved.
- A plan-file reference attachment that preserves the relationship between the transcript and the plan file.

The richer planning workflow has five phases:

1. Understand the request and inspect relevant context.
2. Explore the codebase, often using parallel exploration agents.
3. Design the implementation and update the plan file.
4. Review the plan for gaps and risks.
5. Call `ExitPlanMode` to request approval.

There is also an "interview" workflow. This version is especially relevant to Aunic because it is collaborative and note-first:

1. Start a skeletal plan.
2. Explore enough context to make the plan concrete.
3. Ask the user targeted questions only for decisions the codebase cannot answer.
4. Update the plan after each answer.
5. Continue until the model can either ask the next useful question or request approval.

The important implementation insight is that plan mode changes what tools are allowed and what counts as progress. The model is not merely asked to "make a plan"; the runtime enforces that it cannot start implementation before approval.

### ExitPlanMode
`ExitPlanMode` is the approval boundary.

Important behavior:

- It validates that the model is actually in plan mode before allowing exit.
- It reads the current plan from disk.
- It may show the plan to the user in a permission UI.
- It lets the user approve, reject, edit, or keep planning.
- It restores the previous tool-permission mode after approval.
- It returns a tool result that includes the approved plan and tells the model to proceed.

The approval UI has several useful options:

- Approve and continue with the existing context.
- Approve and clear context, replacing the next turn with an explicit "Implement the following plan" prompt.
- Edit the plan before approving.
- Reject the plan and provide feedback.
- Keep planning.
- In some builds, escalate to a more intensive planning flow.

The "clear context" path is important. Instead of carrying a long exploratory transcript into implementation, the application can start the implementation phase with the approved plan as the primary instruction. The transcript remains available by reference, but the execution context is cleaner.

If the user rejects the plan, the rejection feedback goes back to the model, and the system remains in plan mode. The feedback is treated as planning context, not as implementation approval.

### Slash Command And UI Behavior
The `/plan` command is more than a shortcut:

- If not already in plan mode, it enters plan mode.
- If called with text, it uses that text as the planning prompt.
- If already in plan mode, it shows the current plan.
- `/plan open` opens the current plan file in the editor.

The UI renders plan-mode tool calls distinctly:

- entering plan mode,
- declining plan mode,
- approved plan,
- rejected plan,
- plan markdown,
- plan file path.

This makes planning visible as a first-class activity in the session history rather than burying it in normal assistant text.

### Design Lessons For Aunic
The transferable ideas are:

- Treat planning as a mode, not only as a document.
- Treat the plan file as the source of truth.
- Let the user edit the plan directly before approval.
- Restrict mutation while planning.
- Make approval a real boundary between exploration and implementation.
- Preserve plan references across resume, fork, compaction, and UI navigation.
- Keep rejected plans useful by feeding user feedback back into the planning loop.
- Avoid passing large plan bodies as hidden tool input when a real file can carry the state.

The parts that should not be copied directly:

- The example stores plans in a global/session-oriented plan directory by default. Aunic should associate plans with the source markdown note.
- The example is chat/transcript-first. Aunic is note-first, so the plan should be a companion to the active note, not a replacement for the active note.
- The example uses the plan file as an agent session artifact. Aunic should make the plan part of the user's visible project material.

## Implementing in Aunic
Aunic should implement this as a note-associated planning workflow, not as a clone of the example project's plan mode.

The thesis-level reason is that Aunic's primary context is the markdown note. Chat is not the main workspace. The transcript is a historical record, and tools are useful when they help the user and model improve the note, the project, or the external files. A Plan tool should preserve that shape.

The plan should therefore be:

- attached to a source Aunic note,
- stored as a real markdown file under that note's `.aunic/` directory,
- editable by the user and model,
- visible in the UI near included files,
- excluded from normal note context unless explicitly used for planning or approved implementation,
- used as the approval boundary before work-mode mutations.

### Product Model
The cleanest model is a "Plan Session" associated with a source note.

Definitions:

- Source note: the Aunic markdown file whose note-content, transcript, includes, and project context define the task.
- Plan file: a companion markdown file under `.aunic/plans/`.
- Planning state: a runtime state where the model can explore context and update the plan, but cannot mutate project files.
- Approval state: a UI state where the user reviews the plan and chooses whether implementation may start.
- Implementation state: the normal Aunic work flow after the user approves the plan.

When a plan file is open in the editor, it should not become the context source. This is one of the most important design constraints from the user notes:

> If a prompt is sent with the plan file open in the editor, the context should be sent as if the actual Aunic file was open.

That implies Aunic should split "what is displayed" from "what builds model context."

Recommended state split:

- `context_file`: the source Aunic note used for context building.
- `display_file`: the file currently shown in the editor.
- `active_plan_id`: the selected plan, if any.
- `planning_status`: `none`, `drafting`, `awaiting_approval`, `approved`, or `implementing`.

Today, `active_file` carries too much meaning. It is the displayed document, the source for context, and the target for note tools. Plan support will be much safer if those responsibilities are separated before adding model-facing plan tools.

### Storage
For a source note:

```text
/path/to/project/task.md
```

Plans should live at:

```text
/path/to/project/.aunic/plans/
```

The plan filename should be derived from the first top-level heading in the plan, as requested:

```markdown
# Migrate Task Runner To Persistent Queue
```

becomes:

```text
.aunic/plans/migrate-task-runner-to-persistent-queue.md
```

If there is a collision, append a stable suffix:

```text
migrate-task-runner-to-persistent-queue-2.md
```

or:

```text
migrate-task-runner-to-persistent-queue-a7f3.md
```

I would use a human-readable numeric suffix for ordinary collisions and reserve short hashes for imported or recovered plans.

Each source note also needs an index of associated plans. The most direct option is:

```text
/path/to/project/.aunic/plans/index.json
```

with records like:

```json
{
  "plans": [
    {
      "id": "2026-04-16-migrate-task-runner",
      "source_note": "../task.md",
      "path": "migrate-task-runner-to-persistent-queue.md",
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

The plan file itself should also carry minimal frontmatter so it remains portable if moved:

```markdown
---
aunic_type: plan
plan_id: 2026-04-16-migrate-task-runner
source_note: ../task.md
status: draft
---

# Migrate Task Runner To Persistent Queue

...
```

The index is for fast UI lookup. The frontmatter is for portability, recovery, and human inspection.

Renaming behavior needs care. Since the filename is derived from the top heading, changing the heading can imply a rename. Recommended behavior:

- On creation, slug the initial top heading.
- On explicit save of a plan file, detect heading changes.
- If the derived slug changed and no collision exists, rename the file and update the index.
- If the plan is open in the editor, update `display_file` after the rename.
- If rename fails, keep the old path and only update the title metadata.

Do not silently rename during every keystroke. Rename on save or approval is enough.

### UI
The file menu should keep the current included-files behavior and add a "Plans" section below it.

Suggested shape:

```text
task.md
  Included
    architecture.md
    api-contract.md
  Plans
    [draft] Migrate Task Runner To Persistent Queue
    [approved] Add Import Preview
    + New Plan
```

Selecting an included file should continue to change the active/source note according to existing Aunic behavior.

Selecting a plan should:

- open the plan file in the editor,
- keep the source note as `context_file`,
- set `display_file` to the plan path,
- set `active_plan_id`,
- show a visible indicator such as `Plan: Migrate Task Runner To Persistent Queue (source: task.md)`.

The top bar should make this distinction obvious. The user should never wonder whether they are editing a source note or a plan.

The editor save path also needs to respect the display/context split:

- Saving while a source note is displayed writes the source note.
- Saving while a plan is displayed writes the plan file.
- Prompt submission while a plan is displayed still builds context from the source note.
- `note_edit` and `note_write` should never write transcript rows into a plan file by accident.

### Runtime Context
Aunic's current context builder reads the active file, its included files, and transcript rows from the active file. For plan support, context building should use `context_file`, not `display_file`.

That means the model input for a prompt sent while a plan is open should include:

- source note snapshot,
- source note transcript,
- source note includes,
- user prompt,
- a small system/runtime note saying which plan file is currently open, if relevant.

It should not include the plan body by default. This respects the user's note that plan files are not sent as ordinary context.

Exception: while in planning state, the active plan body should be available to the model because the model must be able to update it. There are two reasonable approaches:

1. Include the active plan snapshot in a plan-specific system attachment.
2. Provide a `plan_read` tool and require the model to call it before editing.

I recommend the first approach for active planning. It reduces tool friction and mirrors the way Aunic gives the model a note snapshot. The system attachment should be clearly labeled as the current plan draft, not as source-note context.

For normal prompts with a plan merely open in the editor, do not include the plan body unless the user explicitly asks about the plan or invokes planning.

### Tool Design
Aunic should add plan-specific tools rather than overloading generic file writes.

Recommended initial tools:

#### `plan_create`

Creates a new plan associated with the current source note.

Input:

```json
{
  "title": "Plan title"
}
```

Behavior:

- creates `.aunic/plans/<slug>.md`,
- writes frontmatter and a starting heading,
- adds an index entry,
- sets `active_plan_id`,
- switches display to the plan file,
- enters planning state.

This can also be triggered by `/plan`.

#### `plan_write`

Replaces the active plan file content.

Input:

```json
{
  "content": "# Plan Title\n\n..."
}
```

Behavior:

- writes the active plan file,
- updates title/status metadata,
- renames if the top heading changed and rename is safe,
- returns a brief result with path, title, and status.

Persistence:

- The file write is durable.
- The tool result should be ephemeral or summarized, similar to `note_write`, because the durable artifact is the plan file itself.

#### `plan_edit`

Applies a targeted edit to the active plan file.

Input should mirror the existing exact-string edit pattern used elsewhere in Aunic:

```json
{
  "old": "Current text",
  "new": "Replacement text"
}
```

Behavior:

- edits only the active plan file,
- refuses to operate if no plan is active,
- updates metadata after edit.

This tool lets the model incrementally refine the plan instead of rewriting the whole file after every discovery.

#### `exit_plan`

Requests user approval for the current plan.

Input:

```json
{}
```

Behavior:

- reads the plan from disk,
- switches UI to approval state,
- presents the current plan markdown to the user,
- lets the user approve, edit, reject, or keep planning,
- if approved, stores approved status and returns an approval result to the model,
- if rejected, returns user feedback and remains in planning state.

Do not put the plan markdown in the tool input. The runtime should read the plan file from disk at approval time. This preserves the example project's best design choice.

Optional later tools:

- `plan_list`: list plans for the current source note.
- `plan_open`: open an existing plan.
- `plan_status`: mark a plan as draft, approved, implementing, implemented, or archived.
- `plan_archive`: hide a plan from the default file menu list.
- `plan_read`: useful if Aunic decides not to inject the active plan snapshot during planning.

### Planning Mode Permissions
Planning should suspend project mutation.

While `planning_status = drafting` or `awaiting_approval`:

- allow source note reads,
- allow included file reads,
- allow filesystem read/search/list tools,
- allow memory/research tools,
- allow `plan_write` and `plan_edit`,
- allow `exit_plan`,
- block `edit`, `write`, and mutating `bash` against project files,
- block note-content mutations unless explicitly designing a workflow where the plan updates the source note.

This is stricter than ordinary `read` mode because it still allows one mutable target: the plan file.

This also fits Aunic's ethos. The user should be able to trust that "we are planning" means the model is not secretly implementing.

Implementation detail:

- The existing `RunToolContext.note_scope_paths()` protects the active note.
- Add `plan_scope_paths()` or `active_plan_path` to protect and authorize the plan file separately.
- Generic `edit` and `write` should still not target plan files during planning. Use plan tools for plan files so plan mutations can update metadata and UI state.
- `bash` should refuse obvious mutations while planning, similar to the current note-scope protection.

### Model Instructions
When Aunic is in planning state, the system prompt should include a compact but firm planning contract.

Suggested instruction:

```text
You are planning changes for the source Aunic note. You may inspect context and use read-only tools. You may update only the active plan file using plan_write or plan_edit. Do not modify project files, source notes, or included files. Keep the plan concrete enough that another model run could implement it. Ask the user only for decisions that cannot be resolved from the available context. When the plan is ready for approval, call exit_plan.
```

Aunic should prefer the interview-style loop:

1. Draft a skeletal plan.
2. Explore the note, transcript, includes, and relevant files.
3. Update the plan as evidence appears.
4. Ask the user only for product decisions, risk tolerance, or missing intent.
5. Request approval with `exit_plan`.

This is more aligned with Aunic than a giant one-shot plan. Aunic is meant to help the user and model build context together in a durable workspace.

### Approval UI
When the model calls `exit_plan`, Aunic should show a plan approval dialog rather than immediately returning a normal tool result.

The dialog should display:

- source note path,
- plan file path,
- plan status,
- rendered plan markdown,
- optional warnings if the plan references files that were not inspected.

Actions:

- `Approve and implement`: approve the plan and start implementation in work mode.
- `Approve only`: mark approved, return to normal note work without starting implementation.
- `Keep planning`: reject the exit request and let the user add feedback.
- `Edit plan`: focus the plan editor so the user can directly change the file.
- `Archive`: mark the plan archived and return to the source note.

The default should be conservative:

- If current work mode is `work`, `Approve and implement` can proceed.
- If current work mode is `read` or `off`, approval should either ask to switch to `work` or use `Approve only`.

Aunic should not silently escalate from read/off into project mutation.

### Implementation Handoff
After approval, Aunic should create an implementation prompt from the approved plan.

Recommended generated prompt:

```text
Implement the approved plan below.

Source note: /absolute/path/to/source-note.md
Plan file: /absolute/path/to/.aunic/plans/plan-title.md

Approved plan:

<plan markdown>
```

This prompt is a handoff artifact. It is not the same as making every future prompt include the plan file.

For small tasks, Aunic can keep the current transcript context. For larger tasks, Aunic should offer a "fresh implementation context" option:

- source note snapshot remains included,
- approved plan becomes the immediate user prompt,
- old exploratory transcript is available by reference but not stuffed into the main model context.

This mirrors the example project's "clear context" option without fighting Aunic's note-first architecture.

### Transcript Semantics
Plan edits should not spam the source note transcript.

Aunic already has a useful distinction:

- persistent tools are logged to the transcript,
- ephemeral note-edit tools write the note but do not become historical noise.

Plan tools should follow the note-edit model:

- `plan_write` and `plan_edit` should be ephemeral by default.
- The durable plan file is the record of the plan.
- High-level lifecycle events can be recorded as compact transcript rows.

Recommended lifecycle rows:

- plan created,
- plan opened,
- approval requested,
- plan approved,
- plan rejected with feedback,
- implementation started,
- implementation completed or abandoned.

These rows should be compact and structured. The transcript should not contain full repeated copies of the plan after every edit.

### Reentry And Resume
If the user runs `/plan` and the source note already has draft plans, Aunic should show those plans before creating a new one.

Good behavior:

- If there is exactly one draft plan, open it and enter planning state.
- If there are multiple draft plans, show a selector.
- If there is an approved but unimplemented plan, offer to implement, revise, or archive it.
- If the user prompt clearly asks for a different task, create a new plan.

Plan reentry instructions should tell the model:

- read the current source note context,
- inspect the existing plan,
- decide whether the new user prompt continues the same plan or needs a new plan,
- update the plan instead of duplicating it when possible.

Resume should be file-based. Because the plan exists under `.aunic/plans/`, Aunic can recover from crashes by reading the index and frontmatter. It does not need to reconstruct the plan from transcript text.

### Suggested File-Level Implementation
Likely implementation areas:

- `src/aunic/plans/service.py`
  - plan ids,
  - slug generation,
  - path resolution,
  - index read/write,
  - frontmatter parse/write,
  - create/list/load/save/rename/archive/status functions.

- `src/aunic/tui/types.py`
  - add `PlanEntry`,
  - add `context_file`,
  - add `display_file`,
  - add `active_plan_id`,
  - add planning/approval status fields.

- `src/aunic/tui/controller.py`
  - add `/plan`,
  - open existing plans,
  - create new plans,
  - keep context-building pointed at `context_file`,
  - wire approval actions to the tool loop,
  - refresh plan list when files change.

- `src/aunic/tui/app.py`
  - add Plans section to the file menu,
  - render plan status,
  - show source-note/plan breadcrumb,
  - add plan approval dialog.

- `src/aunic/tools/plan.py`
  - implement `plan_create`,
  - implement `plan_write`,
  - implement `plan_edit`,
  - implement `exit_plan`,
  - possibly implement `plan_list` and `plan_open`.

- `src/aunic/tools/note_edit.py`
  - include plan tools only while planning or when a plan is active,
  - suspend generic mutation tools while planning.

- `src/aunic/tools/runtime.py`
  - add active plan metadata to `RunToolContext`,
  - add helper for plan path normalization and authorization,
  - add live plan-write helper if the UI should update immediately.

- `src/aunic/context/engine.py`
  - build context from `context_file`,
  - never parse transcript rows from a plan file,
  - optionally attach active plan snapshot only in planning state.

- `src/aunic/providers/shared.py`
  - ensure final user message composition uses the source note snapshot when a plan is displayed,
  - add plan-state information to tool bridge config if needed.

- `src/aunic/transcript/writer.py`
  - support compact synthetic plan lifecycle rows.

### Testing
Important tests:

- A source note at `/tmp/project/task.md` stores plans under `/tmp/project/.aunic/plans/`.
- Plan filenames are derived from the first top-level heading.
- Filename collisions are handled deterministically.
- The plan index lists only plans associated with the current source note.
- Opening a plan changes `display_file` but not `context_file`.
- Sending a prompt while a plan is open sends the source note snapshot and source transcript, not the plan as active note context.
- Planning state allows `plan_edit` and `plan_write`.
- Planning state blocks project `edit`, project `write`, and mutating `bash`.
- `exit_plan` reads the plan from disk, including user edits made after the model's last turn.
- Rejected approval returns feedback and remains in planning state.
- Approved approval marks metadata and creates the expected implementation prompt.
- Plan lifecycle rows are compact and do not duplicate full plan text repeatedly.
- The file menu displays included files and plans separately.
- External edits to plan files refresh the UI and metadata safely.

### Recommended Rollout
The safest rollout is phased.

Phase 1: plan files and UI

- Add `.aunic/plans/` storage.
- Add plan index.
- Add Plans section in the file menu.
- Allow creating, opening, editing, saving, renaming, and archiving plan files.
- Ensure prompt submission from an open plan uses the source note context.

This phase is useful even before model-facing plan tools exist.

Phase 2: planning state and plan tools

- Add `plan_write`, `plan_edit`, and `exit_plan`.
- Add planning system prompt.
- Suspend project mutation while planning.
- Add approval dialog.

Phase 3: implementation handoff

- Add "Approve and implement."
- Generate implementation prompt from the approved plan.
- Support keep-context and fresh-context handoff choices.
- Record compact lifecycle transcript rows.

Phase 4: reentry and advanced planning

- Add robust `/plan` reentry behavior.
- Add plan resume/recovery.
- Add plan review checks.
- Add optional multi-agent exploration later, if Aunic grows that capability.

### Best Shape For Aunic
The best version is not "the model writes a plan and then does it." The best version is:

1. The user starts from a source note.
2. Aunic creates a visible companion plan file.
3. The model explores read-only context and edits only that plan.
4. The user can directly revise the plan as markdown.
5. Approval converts the plan into an implementation handoff.
6. Implementation happens against the original source note and project files.
7. The plan remains attached to the note as durable project memory.

That keeps Aunic's thesis intact. The note remains the primary context. The transcript remains a log. The plan becomes a user-owned working artifact, not a hidden assistant monologue.
