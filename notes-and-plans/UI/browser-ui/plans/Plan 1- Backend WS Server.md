# Plan 1 — Backend WebSocket Server

## Context

This is Plan 1 of 7 for building a browser UI for Aunic (see [browserUI-overview.md](../UI/browser-ui.md/browserUI-overview.md)). Plans 2–7 (frontend scaffold, file explorer, note editor, transcript, prompt editor, PWA/polish) all depend on this server.

The goal: a thin WebSocket server living in the same Python process as the existing CLI that exposes the same backend session logic the TUI uses (`NoteModeRunner`, `ChatModeRunner`, `ToolLoop`, `FileManager`, permission flow) over a structured WS protocol. The browser frontend will be a separate tree that connects to this server and renders.

Per [browserUI-overview.md](../../UI/browser-ui.md/browserUI-overview.md):
- Single-user, LAN-only v1. No auth. Workspace root hardcoded to `/home/ejumps`.
- WS-only for app data; every message carries an `id` for request/response correlation.
- File is the source of truth. Reconnect = re-fetch file snapshot + subscribe live. No event replay buffer.
- On connect the server sends `run_active: true|false` so the client knows whether to show a spinner.
- Mirrors TUI semantics; it is not a new product.

**Decisions already confirmed with user:** single shared global session, full permission wire path, WS endpoint only (no HTTP static serving in Plan 1).

---

## Scope

### In Plan 1
- `aunic serve` CLI subcommand.
- Starlette app with a single `/ws` route.
- Global `BrowserSession` (one per server process) holding run state + permission future.
- `FileWatchHub` fanout over `FileManager.watch()`.
- Message envelope + serializers for `FileSnapshot`, `ProgressEvent`, `TranscriptRow`, `PermissionRequest`.
- Client→server: `hello`, `list_files`, `read_file`, `write_file`, `submit_prompt`, `cancel_run`, `resolve_permission`.
- Server→client: `session_state`, `progress_event`, `transcript_row`, `file_changed`, `permission_request`, `response`, `error`.
- Workspace path validator (symlink-realpath-safe).
- Per-connection bounded outbound queue (back-pressure).
- Unit + integration tests; one end-to-end smoke test.

### Deferred to later plans
- `set_mode` / `set_work_mode` / `set_model` — Plan 3+, when frontend needs them.
- HTTP static serving for built frontend — Plan 7.
- Session-id / multi-session registry — not needed in v1.
- Event replay / seq numbers — intentionally omitted; file is source of truth.
- `loop_event` as a distinct message type — `ProgressEvent` with `kind="loop_event"` already covers it.

---

## Architecture Overview

One Python process. When `aunic serve` runs, it:

1. Constructs providers + `FileManager` + `NoteModeRunner` + `ChatModeRunner` (same wiring as [src/aunic/tui/__init__.py](../../../src/aunic/tui/__init__.py) does today).
2. Constructs one `BrowserSession` bound to workspace_root `/home/ejumps`.
3. Starts Starlette + uvicorn on `127.0.0.1:8765` (configurable).
4. Each WS connection subscribes to the session's event stream and can send requests.

`BrowserSession` is the browser's equivalent of `TuiController` — it owns the run task, permission future, and active prompt pipeline. It is a parallel class, not a refactor of `TuiController`.

### New module tree
```
src/aunic/browser/
  __init__.py        # run_browser_server() entrypoint
  server.py          # Starlette app, /ws route, lifespan wiring, uvicorn launch
  session.py         # BrowserSession (run state, permission future, progress sink)
  connection.py      # ConnectionHandler (per-WS loop, outbound queue, request dispatch)
  messages.py        # envelope types, client→server / server→client discriminated unions, serializers
  paths.py           # resolve_workspace_path() + WorkspacePathError
  watch_hub.py       # FileWatchHub (single FileManager.watch → many subscriber queues)
  errors.py          # typed errors (PathError, RevisionConflict, RunInProgress, etc.)
```

---

## Message Protocol

### Envelope (every message, both directions)
```json
{"id": "uuid4", "type": "<name>", "payload": {...}}
```
- Client requests include an `id`; server's response message echoes the same `id` in its envelope.
- Server-pushed events mint their own `id`; client does not correlate.
- Errors are returned as `{"type": "error", "id": "<request_id>", "payload": {"reason": "...", "details": {...}}}`.

### Client → Server requests

| Type | Payload | Response |
|---|---|---|
| `hello` | `{}` | `session_state` event with full current state |
| `list_files` | `{"subpath": "optional/rel"}` | `{"entries": [{"name", "kind": "dir"\|"file", "path": "rel/path"}]}` |
| `read_file` | `{"path": "rel/path"}` | serialized `FileSnapshot` |
| `write_file` | `{"path": "rel/path", "text": "...", "expected_revision": "..."}` | new `FileSnapshot` OR `error: revision_conflict` |
| `submit_prompt` | `{"active_file": "rel/path", "included_files": [...], "text": "..."}` | `{"run_id": "..."}`; stream of events follows |
| `cancel_run` | `{"run_id": "..."}` | `{"cancelled": true}`; sets `_force_stopped` and cancels `_run_task` |
| `resolve_permission` | `{"permission_id": "...", "resolution": "once"\|"always"\|"reject"}` | `{"ok": true}` |

Mode/model selection is NOT in Plan 1 — `submit_prompt` uses the server's default mode + model. Plan 3 adds `set_mode` / `set_model`.

### Server → Client events

| Type | Payload |
|---|---|
| `session_state` | `{"run_active": bool, "workspace_root": "...", "default_mode": "note"\|"chat", "models": [...]}` — sent on `hello` and whenever state changes |
| `progress_event` | serialized `ProgressEvent` (includes `loop_event` via `kind`) |
| `transcript_row` | serialized `TranscriptRow` (emitted when `append_transcript_row` fires a `file_written` event) |
| `file_changed` | `{"path": "rel/path", "revision_id": "...", "kind": "created"\|"modified"\|"deleted"}` |
| `permission_request` | `{"permission_id": "...", "request": <serialized PermissionRequest>}` |
| `error` | `{"reason": "...", "details": {...}}` |

### Paths
All paths in messages are **workspace-relative POSIX strings** (e.g. `"notes-and-plans/foo.md"`). Server validates + resolves to absolute `Path`. Client never sees absolute host paths.

---

## BrowserSession (`session.py`)

### Responsibilities
- Owns `_run_task: asyncio.Task | None`, `_force_stopped: bool`, `_pending_permission: PermissionRequest | None`, `_permission_future: asyncio.Future | None`, `_permission_id: str | None`.
- Owns default mode (`"note"` for v1), default model (first entry of `_build_model_options`), workspace root.
- Holds `set[ConnectionHandler]` of live subscribers; broadcasts events to all.
- Owns a single `FileWatchHub` and a progress sink that fans out to connections.

### Lifecycle
```python
class BrowserSession:
    def __init__(self, *, workspace_root: Path, file_manager: FileManager,
                 note_runner: NoteModeRunner, chat_runner: ChatModeRunner,
                 providers, ...): ...

    async def attach(self, conn: ConnectionHandler) -> None  # add subscriber, start watch_hub lazily
    async def detach(self, conn: ConnectionHandler) -> None  # remove subscriber

    async def submit_prompt(self, *, active_file: Path, included_files: tuple[Path, ...], text: str, run_id: str) -> None
    async def cancel_run(self, run_id: str) -> bool

    async def request_permission(self, request: PermissionRequest) -> PermissionResolution
    async def resolve_permission(self, permission_id: str, resolution: PermissionResolution) -> None

    async def broadcast(self, type: str, payload: dict) -> None
    def session_state(self) -> dict  # current snapshot for hello
```

### Run flow
1. `submit_prompt` refuses if `run_active` (returns `error: run_in_progress`).
2. Builds a `NoteModeRunRequest` (see [src/aunic/modes/runner.py:32](../../../src/aunic/modes/runner.py#L32)) with a `progress_sink` that serializes each `ProgressEvent` and broadcasts to all connections as `progress_event`.
3. Wraps the run in `self._run_task = asyncio.create_task(...)`. Sets `run_active = True`, broadcasts `session_state`.
4. On task completion (success, exception, or `asyncio.CancelledError`): clears `_run_task`, sets `run_active = False`, broadcasts terminal `progress_event` + new `session_state`.
5. Mirrors `TuiController._force_stopped` at [src/aunic/tui/controller.py:168](../../../src/aunic/tui/controller.py#L168) — `cancel_run` sets the flag, cancels the task, and resolves any pending permission future with `"reject"`.

### Transcript row emission
Every `ProgressEvent` with `kind == "file_written"` carries `details` including `revision_id`, `row_number`, `role`, `type`. When the session sees one for the active prompt run, it reads the last row via `split_note_and_transcript` + row parser (see [src/aunic/transcript/parser.py:20](../../../src/aunic/transcript/parser.py#L20)) and broadcasts a `transcript_row` event. Callers that want the full transcript call `read_file` and parse client-side.

### Permission flow
```python
async def request_permission(self, request: PermissionRequest) -> PermissionResolution:
    loop = asyncio.get_running_loop()
    self._permission_future = loop.create_future()
    self._permission_id = uuid.uuid4().hex
    self._pending_permission = request
    await self.broadcast("permission_request", {
        "permission_id": self._permission_id,
        "request": serialize_permission_request(request),
    })
    try:
        return await self._permission_future
    finally:
        self._pending_permission = None
        self._permission_future = None
        self._permission_id = None
```

- This mirrors `TuiController.request_tool_permission` at [src/aunic/tui/controller.py:1855](../../../src/aunic/tui/controller.py#L1855) — pass `self.request_permission` as the `permission_handler` to the tool runtime.
- `resolve_permission(id, resolution)` validates the id matches `_permission_id` and sets the future result.
- On `cancel_run`, pending future is resolved with `"reject"` so the tool coroutine unwinds.
- On WS disconnect of the last connection mid-permission: future is left pending (freeze-don't-cancel). If a new connection arrives, `session_state` tells it `run_active=True` and a re-emit of `permission_request` is included. If no client returns, the run stays paused indefinitely — acceptable for single-user v1; add a timeout when multi-user becomes a concern.

---

## ConnectionHandler (`connection.py`)

One instance per live WS connection.

### Responsibilities
- Owns a bounded `asyncio.Queue[str]` (max 256) for outbound JSON.
- Spawns two tasks: `_reader` (read + dispatch requests) and `_writer` (drain queue to `ws.send_text`).
- On request dispatch: parse envelope → route by `type` → call the matching `BrowserSession` method → send response with echoed `id`.
- Exceptions caught and returned as `error` envelopes. Unexpected exceptions log + send generic `internal_error`.
- On queue-full: disconnect with close code + log. (Dropping messages silently corrupts client state.)
- On disconnect: call `session.detach(self)`; do not cancel the session's `_run_task`.

### Request routing (rough shape)
```python
REQUEST_HANDLERS = {
    "hello": handle_hello,
    "list_files": handle_list_files,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "submit_prompt": handle_submit_prompt,
    "cancel_run": handle_cancel_run,
    "resolve_permission": handle_resolve_permission,
}
```

Each handler is `async (session, conn, envelope) -> None` and uses `conn.send_response(envelope.id, payload)` or `conn.send_error(envelope.id, reason, details)`.

---

## FileWatchHub (`watch_hub.py`)

Wraps `FileManager.watch()` (see [src/aunic/context/file_manager.py:97](../../../src/aunic/context/file_manager.py#L97)) which is a single-consumer `AsyncIterator`. Hub owns one iterator, reads changes in a long-running task, and fans each change out to every subscriber's callback.

```python
class FileWatchHub:
    async def start(self, paths: Iterable[Path]) -> None
    async def stop(self) -> None
    def subscribe(self, callback: Callable[[FileChange], Awaitable[None]]) -> UnsubscribeToken
```

In Plan 1 there's one subscriber (the session broadcasting `file_changed` events), but the hub is built for N so Plan 3/4 do not have to retrofit it.

**Path set**: initially watches `workspace_root` recursively if `FileManager.watch` supports it; otherwise watches the union of `{active_file} ∪ included_files` for the current run and expands lazily. Confirm [file_manager.py:97](../../../src/aunic/context/file_manager.py#L97) API during implementation.

---

## Paths (`paths.py`)

```python
class WorkspacePathError(Exception): ...

def resolve_workspace_path(subpath: str, *, workspace_root: Path) -> Path:
    """Resolve a client-provided relative path to an absolute Path, rejecting escapes."""
```

Rules:
- Reject `""`, absolute paths, paths containing `..` segments.
- Resolve via `(workspace_root / subpath).resolve(strict=False)`.
- Assert `resolved.is_relative_to(workspace_root)` after resolution (catches symlink escapes).
- Normalize to POSIX for display.

Every client-supplied path on every request goes through this.

---

## Messages (`messages.py`)

- Discriminated TypedDicts (or dataclasses) for every request/event type — NOT Pydantic (the codebase pattern is manual dicts, see serialization helpers in [src/aunic/cli.py](../../../src/aunic/cli.py)).
- Serializers: `serialize_file_snapshot`, `serialize_progress_event`, `serialize_transcript_row`, `serialize_permission_request`. Return plain `dict[str, Any]` ready for `json.dumps`.
- Envelope parser validates: `id` is a string, `type` is in the known set, `payload` is an object. Reject malformed messages with a close code.
- `Path` fields serialize as POSIX relative strings (relative to workspace_root).
- `datetime` fields serialize as ISO-8601 UTC.

---

## CLI wiring (`cli.py` + `browser/__init__.py`)

### Subcommand
Add to `_build_parser`:
```python
serve_parser = subparsers.add_parser("serve", help="Run the browser WebSocket server.")
serve_parser.add_argument("--host", default="127.0.0.1")
serve_parser.add_argument("--port", type=int, default=8765)
serve_parser.add_argument("--workspace-root", default="/home/ejumps")
```

Add to `_dispatch`:
```python
if args.command == "serve":
    return await _run_serve(args)
```

`_run_serve(args)` → `run_browser_server(host=..., port=..., workspace_root=Path(...))` in `browser/__init__.py`.

### Entry (`browser/__init__.py`)
```python
async def run_browser_server(*, host: str, port: int, workspace_root: Path) -> int:
    # Construct providers, FileManager, NoteModeRunner, ChatModeRunner (reuse TUI wiring helpers).
    # Construct BrowserSession.
    # Build Starlette app with one WebSocketRoute("/ws", ...).
    # uvicorn.Server(...).serve()
    # Return 0.
```

Reuse (extract if needed) `_build_model_options` / `_selected_model_index` from [src/aunic/tui/controller.py:140](../../../src/aunic/tui/controller.py#L140) into a shared location (e.g. `src/aunic/models.py` or `src/aunic/shared/models.py`) so `BrowserSession` can call them without importing `tui`.

---

## Dependencies

Add to `pyproject.toml`:
- `starlette>=0.37`
- `uvicorn>=0.30` (standard extras not needed for WS-only)
- `websockets>=12` (uvicorn picks one at runtime; pin explicitly for reproducibility)

Dev dependencies:
- `pytest-asyncio>=0.23` (confirm not already present)
- `httpx>=0.27` (Starlette `TestClient` uses it; likely already transitive via `httpx` already in deps)

---

## Critical files to read / reuse

**Reuse directly (no edits):**
- [src/aunic/context/file_manager.py](../../../src/aunic/context/file_manager.py) — `read_snapshot`, `write_text`, `watch`, revision-id mechanic at line ~80.
- [src/aunic/modes/runner.py](../../../src/aunic/modes/runner.py) — `NoteModeRunner.run(request)`.
- [src/aunic/modes/chat.py](../../../src/aunic/modes/chat.py) — `ChatModeRunner.run(request)`.
- [src/aunic/progress.py](../../../src/aunic/progress.py) — `ProgressEvent`, `ProgressKind`, `emit_progress`.
- [src/aunic/transcript/parser.py](../../../src/aunic/transcript/parser.py) — `split_note_and_transcript`.
- [src/aunic/tools/runtime.py](../../../src/aunic/tools/runtime.py) — `PermissionRequest`, `PermissionResolution`, `PermissionHandler`.
- [src/aunic/domain.py](../../../src/aunic/domain.py) — `TranscriptRow`.
- [src/aunic/context/types.py](../../../src/aunic/context/types.py) — `FileSnapshot`.

**Reference (pattern copy):**
- [src/aunic/tui/controller.py](../../../src/aunic/tui/controller.py) — `_run_task`, `_force_stopped`, `request_tool_permission`, `send_prompt` flow.
- [src/aunic/tui/__init__.py](../../../src/aunic/tui/__init__.py) — provider/runner wiring to mirror.
- [src/aunic/cli.py](../../../src/aunic/cli.py) — subcommand + `_dispatch` pattern; dataclass→dict serialization helpers.

**Modify:**
- [src/aunic/cli.py](../../../src/aunic/cli.py) — add `serve` subcommand branch.
- `pyproject.toml` — add deps.
- Possibly: extract `_build_model_options` / `_selected_model_index` to a shared module.

**Create:**
- `src/aunic/browser/` (all files listed above).
- `tests/test_browser_*.py` (see Verification).

---

## Verification

### Unit tests (`tests/test_browser_paths.py`, `tests/test_browser_messages.py`)
- `resolve_workspace_path`: rejects empty, absolute, `..`, symlink-escaping paths; accepts normal relative paths.
- Envelope parser: rejects missing `id`/`type`, non-string `id`, unknown `type`, non-object `payload`.
- Serializer round-trips for `FileSnapshot`, `ProgressEvent`, `TranscriptRow`, `PermissionRequest`.

### Integration tests (`tests/test_browser_session.py`)
Drive `BrowserSession` directly without a real socket. Use a `FakeConnection` with `asyncio.Queue`-backed inbound/outbound. Inject a fake `NoteModeRunner` whose `run()` yields scripted `ProgressEvent`s into the `progress_sink`.

Cases:
- `hello` → `session_state` with `run_active=False`.
- `read_file` / `write_file` happy path, including revision-conflict rejection.
- `submit_prompt` → events broadcast → final `session_state` with `run_active=False`.
- `cancel_run` mid-run → `_force_stopped`, task cancelled, run terminates.
- Permission flow: runner calls `request_permission` → event broadcast → `resolve_permission` unblocks runner.
- Disconnect mid-permission: future stays pending; reconnect replays `permission_request`.

### End-to-end smoke (`tests/test_browser_e2e.py`)
One test. Start uvicorn on an ephemeral port in a fixture, connect via `websockets.connect`, verify: `hello` → receive `session_state`; `read_file` on a seeded fixture file → receive FileSnapshot. Keep it minimal; integration tests carry the load.

### Manual verification
```
aunic serve --workspace-root /home/ejumps
# in another shell:
python -c "
import asyncio, json, websockets
async def main():
    async with websockets.connect('ws://127.0.0.1:8765/ws') as ws:
        await ws.send(json.dumps({'id': '1', 'type': 'hello', 'payload': {}}))
        print(await ws.recv())
asyncio.run(main())
"
```
Expect a `session_state` event back. Then try `list_files`, `read_file` on a known markdown file, and `submit_prompt` against a real active_file with a trivial prompt.

### Done criteria
- `aunic serve` starts without error and accepts WS connections.
- All protocol messages in the **In Plan 1** scope work round-trip against integration tests.
- A submitted prompt runs end-to-end: progress events stream, permission prompts round-trip, transcript rows push, final `session_state` shows `run_active=False`.
- Workspace path escapes are rejected.
- File-writer revision conflicts are surfaced as errors, never silent.
- Plan 2 can start: has a server to connect to, message types to implement against, and a known port + endpoint.
