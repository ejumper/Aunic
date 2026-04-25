# How Workflow Task Tools Work

Workflow tasks are a structured todo list the model creates and updates to track multi-step work across a session. The relevant tools are TaskCreate, TaskGet, TaskList, and TaskUpdate.

---

## How it works in coding-agent-program-example

**Storage**: Tasks are stored as individual JSON files in a per-session directory resolved by `getTaskListId()`. The path is `~/.claude/tasks/<session-id>/<id>.json`. Each file contains a full `Task` object:

```ts
{
  id: string,
  subject: string,
  description: string,
  activeForm?: string,     // present-continuous label for spinner: "Running tests"
  status: 'pending' | 'in_progress' | 'completed',
  owner?: string,          // agent name — used in swarms (not relevant for Aunic)
  blocks: string[],        // task IDs this task blocks
  blockedBy: string[],     // task IDs that block this task
  metadata?: Record<string, unknown>,
}
```

**ID assignment**: A `.highwatermark` file tracks the highest ever-used integer ID to prevent reuse across resets.

**Locking**: Because swarm agents can run in parallel, the task directory uses a file-based lock around all create/update/delete operations.

**Tools and their behavior**:

- **TaskCreate** — writes a new JSON file with `status: 'pending'`. Auto-expands the task panel in the UI.
- **TaskGet** — reads a single JSON file by ID and returns its fields.
- **TaskList** — reads all JSON files in the directory. Strips `blockedBy` entries that point to already-completed tasks before returning.
- **TaskUpdate** — reads the existing task, applies partial field updates (subject, description, status, metadata), writes the file back. Special-case `status: 'deleted'` deletes the file. Supports `addBlocks`/`addBlockedBy` for dependency edges.

**Prompt guidance to the model**: All four tools carry detailed system prompt additions that tell the model *when* to create tasks (3+ step work, multi-task user requests) and when *not* to (trivial single-step tasks). Key rules:
- Mark a task `in_progress` before beginning work on it
- Mark it `completed` immediately after finishing
- Check `TaskList` before creating to avoid duplicates

---

## How to implement in Aunic

**Storage**: A session-state dict backed by a JSON sidecar file. No file locking needed — Aunic runs as a single Python process per session.

- In-memory: `session_state.tasks` — a `dict[int, TaskDict]`
- On disk: one file at `<notes-dir>/.aunic-tasks-<session-id>.json`, written after every mutation. Survives browser refresh.

**Task schema**:
```python
{
  "id": int,
  "subject": str,
  "description": str,
  "status": "pending" | "in_progress" | "completed",
  "metadata": dict | None,
}
```
No `blocks`/`blockedBy` or `owner` needed — those are swarm-agent features.

**Tool implementations** — add to `src/aunic/tools/`:

- `task_create.py` — appends a new task dict with auto-incremented integer ID and `status: pending`. Returns the ID.
- `task_get.py` — looks up by ID, returns all fields or a "not found" message.
- `task_list.py` — returns all tasks; optionally filter by status.
- `task_update.py` — updates mutable fields (subject, description, status, metadata). `status: deleted` removes the entry entirely.

**System prompt guidance**: Tell the model to use tasks for work requiring 3+ steps, to mark tasks `in_progress` before starting, and `completed` immediately after — same pattern as coding-agent-program-example.
