# Aunic Run Guide

This guide explains how to launch and use the current terminal UI, plus the backend-only note/chat commands that still exist underneath it.

## Recommended Way To Run

If you are running directly from the repo, use:

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli --help
```

If you have already installed the project into the virtualenv, this also works:

```bash
.venv/bin/aunic --help
```

The examples below use the repo-safe form:

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli ...
```

## Quick Health Check

Before launching the UI, it is worth checking the providers:

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli doctor
```

You can also check a single provider:

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli doctor --provider codex
PYTHONPATH=src .venv/bin/python -m aunic.cli doctor --provider llama
```

Important note about llama:

- if `doctor` says llama.cpp is not running but the startup script is available, that is still a usable state
- Aunic will try to start llama.cpp automatically on first real use
- that means `doctor` can show llama as effectively ready even when nothing is currently listening on `:8080`

## Launching The Terminal UI

### Simplest launch

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui notes/today.md
```

That opens the TUI in `note` mode using the default `codex` provider.

### Start in chat mode

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui notes/today.md --mode chat
```

### Start with the local llama.cpp model

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui notes/today.md --provider llama
```

### Start with extra included files

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui notes/today.md \
  --include notes/reference.md \
  --include notes/scratch.md
```

### Start with optional overrides

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui notes/today.md \
  --provider codex \
  --model gpt-5.4 \
  --reasoning-effort medium \
  --display-root ~/HalfaCloud/Aunic \
  --cwd ~/HalfaCloud/Aunic
```

## What The UI Does

The TUI has five main areas:

- `top_bar`: active file name, clickable to switch files
- `text_editor`: editable active file buffer
- `transcript_view`: rendered transcript pane with filters, sorting, expansion, and row deletion
- `indicator_area`: status, errors, tool/run progress, completion notices, and synthesis notices
- `prompt_editor_box`: prompt input plus controls

The TUI is only a frontend. Note mode, chat mode, search, fetch, and file edits are handled by the backend runners.

## Basic Workflow

### Note mode

1. Open a markdown note in the TUI.
2. Edit the note directly in the main editor if needed.
3. Type a prompt in the bottom prompt box.
4. Send it with `Ctrl+R` or the `Send` button.
5. Aunic saves the active file before sending.
6. The model works through the normal note-mode loop and may edit the file.
7. The indicator area shows progress, the natural-stop completion message, and any synthesis-pass status.

### Chat mode

1. Switch to chat mode with `F4` or the mode button.
2. Type a normal prompt in the bottom box.
3. Send it with `Ctrl+R`.
4. Aunic appends the user prompt and the assistant response into the active note.

Important:
- prompts are always taken directly from the prompt box in both `note` and `chat` mode
- removed slash commands such as `/prompt-from-note` and `/plan` are rejected in the TUI

## Keyboard Controls

- `Ctrl+R`: send the prompt
- `Ctrl+S`: save the active file
- `Ctrl+Q`: quit the TUI
- `F2`: open the file picker
- `F3`: open the model picker
- `F4`: toggle `note` / `chat` mode
- `Tab`: fold or unfold at the current editor line
- `Esc`: close the active dialog

## Mouse Controls

- click the file name in the top bar to open the file picker
- click the control row buttons for:
  - send
  - mode toggle
  - work-mode toggle (`off` / `read` / `work`)
  - model picker

## File Behavior

- The active editor uses save-on-send.
- If you switch files while the current buffer is dirty, Aunic asks:
  - save
  - don't save
  - cancel
- If the active file changes on disk while the buffer is clean, Aunic reloads it automatically.
- If the active file changes on disk while you have unsaved edits, Aunic warns instead of overwriting your buffer.

## Folding

Phase 8 folding is enabled in the main editor.

Foldable structures include:

- markdown headings
- indented blocks
- ordered and unordered lists

These legacy managed sections are folded by default the first time a file is opened in a TUI session:

- `# Search Results`
- `# Work Log`

Fold state is session-only. It does not persist after the TUI exits.

## Model Selection

The current model picker switches between two hardcoded provider/model lanes:

- `Codex` using the configured default Codex model
- `Llama` using the configured default llama.cpp model

`--provider` and `--model` control the initial selection when the TUI starts. The picker lets you switch after launch.

## Current Scope

What is implemented:

- full-screen prompt_toolkit TUI
- note/chat mode dispatch
- work/read/off mode dispatch
- rendered transcript pane
- live status updates at the turn/tool level
- file switching
- model switching
- session-scoped folding
- mouse-capable controls

What is intentionally not implemented yet:

- token streaming
- include-management UI
- persistent fold state across sessions
- broad slash-command support beyond `@web`

## Good First Test Runs

### Note mode with Codex

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui build_plans/v1-notes.md --provider codex
```

Then type something like:

```text
Add a short testing checklist section near the bottom.
```

### Note mode with llama

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui build_plans/v1-notes.md --provider llama
```

Then try a small focused edit prompt first.

### Chat mode

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli tui build_plans/v1-notes.md --mode chat --provider codex
```

Then ask a normal question in the prompt box and send it.

## Backend-Only Commands

The old backend entrypoints still exist and are useful for debugging.

### Note mode backend run

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli note run notes/today.md --prompt "Tighten this note."
```

Or:

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli note run notes/today.md --prompt "Tighten this note."
```

### Chat mode backend run

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli chat run notes/today.md --prompt "Summarize the last section."
```

### Context inspection

```bash
PYTHONPATH=src .venv/bin/python -m aunic.cli context inspect notes/today.md
```

## If Something Goes Wrong

- run `doctor`
- try `--provider codex` first if the local model is behaving strangely
- use the backend-only `note run`, `chat run`, or `context inspect` commands to isolate whether the problem is the UI or the backend
- remember that work-mode permissions can still reject reads, writes, or shell use depending on the current policy
