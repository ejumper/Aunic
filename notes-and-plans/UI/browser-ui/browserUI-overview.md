# Browser UI
Don't Overthink this. The plan is to take what the TUI does and create a webpage that does the same thing. The main process here is how does the TUI do <X>, how can the browser do the same thing and how can it look slightly cleaner given that the TUI design restrictions don't exist.
- I have tried to outline the most important decisions below, if I have left anything out that needs an informed decision, ask me, or make a reasonable choice based on best practices.
- browser-prototype.png offers a very basic idea of what the main UI should look like. Use it as a starting point, do not treat it as a bible. Use emojis for icons for now. I will replace them with images/svgs later.
- `security/security-overview.md` outlines the basic security features I want. I am not looking for a full implementation of these yet. This will be LAN only for now and I'm on a secure network. That being said, build in a way that's conducive to them being added later and if you judge that its reasonably easy to implement them now, you might as well go ahead and do them.
    - Things from security-overview.md I specifically DON'T want right now...
        - login username and password
        - Workspace root configuration required for browser mode (this should be hardcoded for now to /home/ejumps)
        - permissions should be set to mirror the ~/.aunic permissions for now
        - HTTP off-localhost is fine for now
        - Do not Add separate mode safety profiles yet
        - no "first time setup" portal
        - everything under "## Things to Consider" can be ignored for now.

## UI
As much of the logic as possible should be kept on the backend. The TUI vs the Browser should as much as possible be wrappers for interacting with the backend. 
- The browser UI is a way to interact with the computer that is running Aunic. It is not an isolated "app", its a remote control for the computer Aunic is running on.
At its core, the browser interface should be a near perfect clone of the TUI
That being said...
- consider the limitations of TUIs. Mainly that they must fit into terminal cells and have limited shortcut compatibility
    - These should be improved on for aesthetics and ease of use. Think of the difference between claude code in the terminal and claude workspace. This is the aesthetic difference I am talking about. 
        - things like variable font sizes, rounded corners, better rendering etc. should be taken advantage of.
- consider what the TUI gets implicitly by being a part of the terminal...
    - file system navigation, creating new files/dirs, etc. these need to be added to the browser interface.
- Mobile should be the first class use case.
- built to the limitations of ios pwa, since that is the most restrictive.
- stream via web sockets
- for the cmd



*note: when in doubt the answer is do it like the TUI, all this is, is a different front end*


### Browser Session / Active Note Model
- Frontend language/framework: TypeScript + React + Vite.
- React is preferred over Svelte because the browser UI will be state-heavy, React is more familiar, and its ecosystem is stronger for editor-adjacent app UI such as virtualized transcript rows, dialogs, file trees, and command palettes.

### File Editor
- CodeMirror 6 should be integrated directly as an imperative editor component rather than treated as normal React-controlled text input.
- Use CodeMirror 6 as the browser note editor.
- The editor edits `note-content` as source text; Aunic should not use a rich-text editor as the source of truth.
- Enable soft word wrapping.
- Soft wraps should preserve indentation. If CodeMirror's default wrapping is insufficient, implement this with a custom extension/CSS layer.
- Implement live markdown rendering with CodeMirror syntax parsing and decorations.
- Markdown rendering should be suppressed for the active cursor line/block so the user can edit the raw markdown syntax directly.
- Use CodeMirror keymaps as the baseline for common desktop editing shortcuts, with Aunic-specific bindings layered on top.
- Prototype table rendering early because it is the highest-risk editor behavior.

### Transcript
- The browser transcript should behave like the TUI transcript in structure and semantics.
- Keep the existing transcript controls: `[ + ]` for full/maximized view and `[ v ]` for close/collapse.
- Add browser-native resizing: the user should be able to drag/slide the transcript to the height they want.
- Chat, tool, and search rendering can be visually nicer than the TUI version, but should not be materially different in behavior or meaning.
- Transcript text/content should be normally selectable and copyable in the browser. Prefer browser text selection over the TUI-specific right-click / `y` copy method where possible.
- The transcript remains a rendered view of the parsed transcript rows, not a separate chat database.
- Do not virtualize the transcript list. Render all rows as real DOM nodes. Aunic transcripts are markdown tables and won't reach the row counts where virtualization is necessary; adding it would break native browser text selection across rows.

### Prompt Editor / Composer
- Use CodeMirror 6 for the prompt editor too, but with a different extension set than the file editor.
- The prompt editor should be plain multiline source text, not a rich-text editor.
- Share a lower-level CodeMirror wrapper between the file editor and prompt editor, then configure separate `NoteEditor` and `PromptEditor` behaviors on top.
- Enable soft word wrapping and use CodeMirror keymaps as the baseline for common desktop editing shortcuts.
- Add Aunic-specific keybindings on top, such as send/run and command-menu behavior.
- The prompt editor should support syntax highlighting and autocomplete for Aunic prompt features such as `@web`, `@rag`, `@docs`, slash commands, and relevant edit commands.
- The prompt editor should not use the note editor's markdown rendering behavior by default. Do not render tables, headers, bold, or italic syntax unless later testing shows prompt markdown rendering is useful.
- Enter/send behavior should be chosen intentionally for desktop and mobile. Desktop can use a keyboard shortcut for send while Enter inserts a newline; mobile must always have an obvious send button.
- The prompt editor should grow dynamically up to a max height, then scroll internally.

### Indicator Area
- The browser indicator area should behave the same as the TUI indicator area: one current/most-relevant status message showing what is happening now or what most recently happened.
- Indicator text must be selectable/copyable in the browser UI.

### File Explorer
- Use React Aria Tree for the browser file explorer.
- React Aria Tree is preferred because the file explorer needs strong accessibility, keyboard behavior, and touch/mobile behavior more than it needs a desktop-only file-manager feel.
- The file explorer should be a frontend tree over backend-provided workspace data, not a browser-owned file manager.
- File access is always scoped by the backend. Initially the workspace root can be hardcoded; later it should be configurable through Settings, with the security details defined in `security/security-overview.md`.
- The frontend should send intentional actions such as open note, create markdown file, rename, delete, or include file. The backend must validate every path/action against the workspace scope.
- Keep the first implementation conservative: open/select files and folders first; add creation, deletion, renaming, drag/drop, and broader file operations only when the backend permission/security model supports them.

### Settings
*(note: this references the gear icon in the top left of browser-prototype.png, not things like model picker, mode switchers, etc.)*
- Initial implementation: settings is a dead button / placeholder for future implementation.
- The button can be present in the UI to reserve the interaction point, but it should not require settings backend work in the first browser UI pass.

### Browser Server / Transport
- Do not use Next.js/SSR for the initial browser UI; this is a LAN-served app/PWA shell talking to the Aunic backend over WebSocket.
- Use WebSocket only — no HTTP for load/save/file tree. A mixed HTTP+WS approach creates a race condition (e.g. a `file_changed` WS event arriving mid-HTTP file tree fetch) that requires reconciling two state sources. WS-only with request/response correlation IDs eliminates this class of bug.
- Every WS message has an `id` field. Frontend requests include an id; server responses echo it back. The frontend keeps a `pendingRequests` map to correlate responses.
- Explicit message types rather than generic blobs. WS message types map 1:1 to existing backend dataclasses (ProgressEvent, LoopEvent, TranscriptRow, FileSnapshot, PermissionRequest) via a thin Pydantic/JSON adapter. Don't invent a parallel vocabulary.

### Backend Ownership
- browser never parses raw full markdown as the source of truth
- backend splits note-content / transcript
- backend validates workspace paths
- backend owns run state
- backend owns model/tool execution
- frontend owns rendering, local UI state, editor buffers, selection, and layout
- be reasonable about this and don't over-apply. Editor latency (typing, wrapping, decorations, folding, find) must be fully local — don't ever round-trip a keystroke. Don't make a remote-rendered editor.

### Additional Notes
- Optimistic-write / expected_revision. file_manager.py:80 — every save requires the revision id. Browser must hold and forward it; a "last write wins" REST endpoint will silently corrupt notes.
- Save-on-send. TUI saves the active file before any prompt run; the browser needs identical behavior or the run sees stale content.
- External file change reload. Watcher behavior (auto-reload when clean, warn when dirty) — define how the WS pushes file change events.
- Edit-command syntax in the note editor. @>> <<@, !>> <<!, %>> <<%, $>> <<$, ">> <<" all need CM6 decorations. Listed for the prompt editor only.
- Folding. TUI has Obsidian-style folding for headings/lists/indents (folding.py), with managed-section defaults. CM6's foldGutter handles this but you have to wire it.
- Transcript controls parity. TUI has sort order toggle, per-row expand/collapse, row delete, right-click copy, and bash-row collapse/expand, browser should have this too.
- Wire format alignment: The backend already emits ProgressEvent, LoopEvent, TranscriptRow, FileSnapshot, PermissionRequest. WS message types map 1:1 to these existing dataclasses (probably via a thin Pydantic/JSON adapter). Don't invent a parallel vocabulary.
- backend splits note/transcript before sending. Editor only ever sees note-content; transcript view receives parsed rows. The full file is never round-tripped to the browser.
- For now assume one writer at a time for files. This is single-user only
- browser server live inside the same Python process as the existing CLI
- prompt send auto saves
- Run-state hydration: Because the file is the source of truth, "reconnect" is just: reload the current file snapshot (note-content + parsed transcript rows) and start receiving live events from now. The transcript already contains everything that happened. The one extra thing needed: on the initial WS handshake, the server must send a run_active: true/false flag so the frontend knows whether to show a spinner. Without that, the user reconnects and doesn't know if a run is in progress or idle. (this also applies for PWA reconnect)

### Test Strategy
- unit tests for backend API/session behavior
- frontend component tests only where useful
- Playwright smoke tests for browser UI



## End State
The browser interface v1 is "finished" when the user can connect to the site, browse files (within the preconfigured scope) create new markdown files, create directories, delete markdown files and directories, open markdown file, edit the text of those files, view the transcript, filter the transcript, send prompts, switch modes, switch models, use edit, slash and @ commands, send prompts to the model and have them do work based on it, etc. all from a browser.


*note: to reiterate, I'm not looking to do anything radical here, I just want the TUI translated to a browser interface. This is also a first draft you're building, So lots of tweaks and reworks are expected especially for getting it usable on mobile. This is about building a working starting point to iterate on, not a finished project.*

# Implementation Plan
each plan is a shippable vertical slice that unblocks the next:

## Plan 1 - Backend WS Server
[status: finished]
Python WebSocket server living in the existing process. Message types (FileSnapshot, TranscriptRow, ProgressEvent, PermissionRequest, run_active), session lifecycle, file snapshot delivery, path validation. This is the foundation everything else plugs into.

## Plan 2 - Frontend Scaffold + WS Client
[status: finished]
Vite + React + TS project, WS connection, pendingRequests correlation map, message routing. No real UI yet — just "can connect, can exchange typed messages."

## Plan 3 - File Explorer
[status: finished]
React Aria Tree over backend-provided workspace data. Browse, open, create, delete. Triggers real backend file ops over WS.

## Plan 4 - Note Editor
[status: finished]
CodeMirror 6, markdown rendering, edit-command decorations, save-on-send, optimistic writes with revision IDs.

## Plan 5 - Transcript View
[status: finished]
Render parsed rows, transcript controls (expand/collapse, sort, delete, copy), drag-resize.

## Plan 6 - Prompt Editor + Run
[status: finished]
CodeMirror 6 prompt editor, send/run flow, mode/model switching, indicator area, run_active hydration on reconnect.

## Plan 7 - PWA + Polish
[status: finished]
PWA manifest, iOS edge cases, Playwright smoke tests.
