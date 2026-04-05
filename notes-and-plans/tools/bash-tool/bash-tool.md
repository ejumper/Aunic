## The Bash Tool
The model uses `bash` in `work-mode` to execute shell commands in the project environment.

- `bash`
    - `command`
        - required string
        - the shell command to execute
        - prefer one shell command string, not an ad hoc script file
        - independent commands should usually be separate `bash` tool calls so they can run in parallel
        - dependent commands may be chained with `&&`, `||`, or `;`
        - raw newlines should not be used to separate unrelated commands
        - raw newlines are allowed when they are part of one shell construct such as a heredoc or multiline quoted command
    - `timeout`
        - optional integer in milliseconds
        - defaults to Aunic's configured bash timeout
        - capped at Aunic's configured bash timeout max
    - `description`
        - optional string
        - concise active-voice explanation of what the command does
        - used for permission UI, activity text, and transcript rendering
    - `run_in_background`
        - optional boolean
        - when true, Aunic starts the command as a background task and returns immediately with a background task id
    - `dangerouslyDisableSandbox`
        - optional boolean
        - only available when `work-mode` sandboxing supports a one-command override
        - should normally be omitted
        - should only be used when the user explicitly asks to bypass the sandbox or a previous failure clearly shows sandbox restrictions as the cause

Internal-only execution data may exist during the permission flow, but it must not be model-addressable. The key example is a hidden precomputed `sed -i` edit payload used to apply exactly what the user previewed rather than re-running `sed`.

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## Design Goal
Implement `bash` as close as possible to `coding-agent-program-example`:
- fresh shell process per command, not one long-lived PTY
- session-scoped reconstructed shell state rather than true shell-process persistence
- AST-aware permission and safety checks
- read-only fast path
- sandbox-aware execution
- background tasks and progress streaming
- persisted large-output handling

Retain these Aunic-specific behaviors:
- every `tool_call`, `tool_result`, and `tool_error` remains a durable transcript row
- `tool_error` vs `tool_result` stays explicit in the transcript even if a provider only has one tool-result transport shape
- `work-mode` must still protect note content from shell-based mutation

## When The Model Should Use `bash`
`bash` is for command execution, not for general repo inspection or file editing when dedicated tools are better.

- use `read`, `grep`, `glob`, and `list` for reading/searching files
- use `edit`, `write`, and `patch` for file changes
- use Aunic's dedicated web research tools / flows when the task is web research rather than normal project command execution
- prefer separate `bash` calls when commands are independent and can run in parallel
- use one `bash` call with `&&` when commands depend on one another
- prefer absolute paths over `cd`
- only rely on `cd` when changing shell state is itself part of the task
- do not use sleep loops to poll for progress
- if `run_in_background` is available and the result is not needed immediately, prefer it over blocking the conversation

The system prompt for `work-mode` should enforce this strongly, similar to `coding-agent-program-example`:
- search with search tools, not `find`/`grep`, unless Aunic intentionally exposes shell-backed embedded search
- read files with read tools, not `cat`/`head`/`tail`
- edit files with edit tools, not ad hoc `sed`/`awk`/`perl -pi`
- write files with write tools, not `echo > file` or heredoc file writes

## Session-Scoped Shell State, Not A Persistent Shell Process
Unlike the current plan, Aunic should not keep one live shell process for the whole `work-mode` session. To match `coding-agent-program-example`, each `bash` execution should spawn a fresh shell process.

What persists across `bash` calls is modeled session state, not a live shell:
- current working directory
- shell snapshot derived from the user's login shell config
- session environment overlays intentionally managed by Aunic
- background tasks that continue after the initiating tool call returns

What should not be promised to persist automatically:
- arbitrary `export FOO=bar` from one bash call
- temporary shell locals
- ad hoc functions defined inside a prior bash call
- ad hoc aliases defined inside a prior bash call
- job table state from a prior shell process

This is an intentional divergence from a true persistent shell because it is closer to the example implementation and has major benefits:
- easier sandbox wrapping
- easier cancellation and timeout handling
- easier backgrounding
- easier crash recovery
- fewer long-lived shell-state bugs
- deterministic per-call startup

## Shell Bootstrap And Reconstructed State
### Choosing the shell
When Aunic needs a shell provider:
- first use an explicit Aunic shell override if configured and valid
- otherwise prefer `$SHELL` if it points to a supported POSIX shell such as `bash` or `zsh`
- otherwise search common shell locations and `which`
- if nothing suitable is found, fail with a friendly shell-startup `tool_error`

### Session snapshot
At session startup, or lazily on the first bash call, Aunic should build a shell snapshot file similarly to `coding-agent-program-example`.

The snapshot should be created by:
- launching the chosen shell as a login shell
- sourcing the user's shell config
- capturing shell functions
- capturing shell options
- capturing aliases
- adding Aunic-specific shell setup such as embedded search wrappers when applicable

The snapshot file is session-scoped:
- stored in Aunic-owned temp/config state
- reused across later `bash` calls
- cleaned up when the session ends

If the snapshot file disappears:
- Aunic should fall back to normal login-shell startup for that command
- Aunic should recreate the snapshot when practical
- this is not a transcript-visible shell reset in the old "persistent shell died" sense, because there is no persistent shell process anymore

### Session environment overlays
To stay close to the example, environment persistence should come from explicit Aunic-managed mechanisms, not inferred shell side effects.

Supported persisted sources should include:
- a session env map managed by Aunic
- hook-produced env fragments if Aunic adopts hook-driven session env updates
- optionally a `CLAUDE_ENV_FILE`-style integration if Aunic wants external runners to inject env for later bash calls

If these overlays change, Aunic should invalidate any cached combined env script and apply the new script on the next bash command.

### Current working directory
Aunic should track cwd in session state.

Each command should:
- start from the session cwd
- append a `pwd -P` write to a temp file at the end of the wrapped command
- after the process exits successfully enough to have produced that file, update the session cwd from it

If the current cwd no longer exists on disk:
- recover to the original project working directory if possible
- otherwise return a shell-startup `tool_error`

## How A `bash` Tool Call Starts
1. The model emits a tool call
    - The provider returns an assistant message containing a `bash` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The tool call arguments should contain `command`, and may also contain `timeout`, `description`, `run_in_background`, and `dangerouslyDisableSandbox`.

2. Aunic parses and validates the arguments
    - `command` must exist and must be a string
    - `timeout`, if present, must be numeric
    - `description`, if present, must be a string
    - `run_in_background`, if present, must be boolean-like
    - `dangerouslyDisableSandbox`, if present, must be boolean-like
    - if `timeout` is missing or `<= 0`, use the default timeout
    - if `timeout` is above the max, clamp it to the max

3. Aunic records the `tool_call` row
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `bash` is a persistent `work-mode` tool in the transcript sense, it should also be written to the `transcript`.
    - This happens even if the command is later rejected or errors before execution. The model did in fact attempt the call, so the record should exist.

## Preflight Validation
Before the command is allowed to run, Aunic should perform a local preflight pass.

### Basic validation failures
These should produce a `tool_error` result without spawning a shell:
- missing `command`
- invalid JSON arguments
- invalid `timeout` type
- invalid `description` type
- invalid `run_in_background` type
- invalid `dangerouslyDisableSandbox` type
- empty command after trimming whitespace

### Prompt-level steering rather than giant hard bans
To stay close to `coding-agent-program-example`, Aunic should not rely on a giant baked-in ban list for normal shell tools like `curl`, `wget`, or `gh`.

Instead:
- the system prompt should strongly steer the model toward dedicated tools
- the permission system, sandbox rules, path validation, and safety parser should decide whether the command may run
- unsupported interactive/browser flows can still be rejected if they fundamentally do not fit the tool model

### Protecting `note-content`
This is an Aunic-specific retained requirement.

`work-mode` should not allow `bash` to become a back door around the `note-mode` / `work-mode` split.
- if the command explicitly targets the active note file or another included note file for mutation, reject it with `tool_error`
- if the command only reads those files, allow it under normal read permissions
- if static analysis cannot tell whether the command writes to a note file, fall back to the normal permission flow rather than guessing success

## Permission Flow
After basic validation, the command goes through the shared `work-mode` permission system.

The high-level order should be close to `coding-agent-program-example`:
1. AST/syntax safety parse
2. sandbox auto-allow checks, if enabled
3. exact-rule deny/ask/allow checks
4. classifier-driven deny/ask checks, if enabled
5. shell-operator and subcommand analysis
6. path and output-redirection validation
7. compound command merge logic and suggested rule generation

### Parse and safety analysis
Use a real shell-aware parser when possible:
- preferred: tree-sitter Bash or equivalent AST parser
- fallback: a shell-quote/token parser for simpler cases

The parser should classify commands into:
- `simple`
- `too_complex`
- `parse_unavailable`

`too_complex` should not be silently allowed:
- if an explicit deny rule already matches, deny
- otherwise ask

Examples that should tend to ask unless explicitly allowed:
- process substitution
- hard-to-analyze command substitution
- shell-expansion-heavy redirection targets
- control-flow structures the safety layer cannot confidently normalize

### Read-only fast path
Like the example, Aunic should have a strong read-only validation pass.

If a command is confidently read-only:
- it can be auto-allowed before slower permission paths
- it can be marked concurrency-safe

This validator should be command-aware, not just regex-on-tool-name:
- maintain allowlists for safe read-only commands and safe flags
- validate flags, not just the base command
- reject hidden code execution, unexpected file writes, and network writes

It should also contain the example-style security caveats:
- some `git` commands stop being safely auto-allowable in suspicious cwd situations
- `cd` plus `git` in a compound command should not be treated as simple read-only
- UNC/network-like paths should force at least `ask`

### What `bash` permissions match against
`bash` rules should match parsed commands, not just the literal tool name.
- `git status --porcelain` should match a pattern like `git *`
- `git commit -m "x"` should match a pattern like `git commit *`

Use normalized argv matching where possible:
- strip safe env-var prefixes when matching
- strip safe wrapper commands such as `timeout` when appropriate
- do not allow wrappers like `env`, `sudo`, or bare shells to produce dangerously broad suggested rules

If one `command` string contains multiple shell commands, each parsed subcommand should be checked.
- if any subcommand is `deny`, the whole tool call is denied
- otherwise if any subcommand is `ask`, the whole tool call asks
- otherwise the tool call is allowed

Within one permission object, the last matching rule wins.

### Operator-aware permission checks
Close to the example, Aunic should explicitly analyze:
- `&&`
- `||`
- `;`
- `|`
- `>`
- `>>`
- `>&`

Important rule:
- even if individual pipe segments are allowed, Aunic must still validate the original command for dangerous redirections and redirect-target safety

### Path validation and `external_directory`
`external_directory` should remain part of the design, but the implementation should look more like the example's path validator than a bash-specific special case.

Path validation should:
- inspect command arguments for supported path-taking commands
- inspect output redirections separately
- resolve paths relative to the current session cwd
- understand `--` end-of-options handling
- catch dangerous removal targets like `/`, `/etc`, or equivalent critical paths
- ask on writes in compound commands that include `cd`, because the final cwd is ambiguous

`external_directory` semantics:
- if the session cwd is already outside the project root, the external-directory policy still applies
- if the command references paths outside the project root, the external-directory policy applies through path validation
- if those paths are explicitly allowed, execution can continue
- otherwise the decision should be `ask` by default

### Mode-based permission behavior
If Aunic has mode-based permission shortcuts similar to `acceptEdits`, bash should participate consistently.

The closest-example behavior is:
- in an "accept edits" style mode, auto-allow simple filesystem mutating commands such as `mkdir`, `touch`, `rm`, `rmdir`, `mv`, `cp`, and safe `sed`

If Aunic does not have that mode yet, this section is future-facing and can remain unimplemented at first.

### Suggested safe patterns for "always allow"
For `always`, the bash tool should provide a suggested safe pattern, but it should follow the smarter strategy used by the example:
- use a two-word prefix when that creates a stable safe rule, for example `git commit *`
- if the command includes a heredoc, extract the stable prefix before the heredoc and suggest that
- if the command is multiline, suggest a prefix rather than saving the whole multiline text
- never suggest a dangerous broad prefix like `bash *`, `sh *`, `env *`, or `sudo *`
- cap the number of suggested rules surfaced from one compound command prompt

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.

- `once`
    - allow this one execution only
- `always`
    - allow future matching executions for the rest of the current Aunic session
- `reject`
    - deny the request

The permission prompt itself is UI state, not a transcript row.
- the transcript should contain the `tool_call`
- then either a `tool_result` or a `tool_error`

## `sed -i` Special Handling
To get as close as possible to the example, Aunic should special-case safe in-place `sed` edits.

Plan:
- detect simple `sed -i` substitution commands
- parse the target file and substitution
- show a file-edit-style permission preview rather than a generic bash approval
- after approval, apply the already-previewed new file content directly
- do not re-run the original `sed` command after approval

This has two benefits:
- the user approves the exact edit that will be applied
- the edit can integrate cleanly with Aunic file history / transcript rendering / note protections

The hidden payload used for the actual apply step must remain internal-only and never appear in the model-visible schema.

## Running The Command
If validation and permissions pass, Aunic should run the command through a fresh shell process using the reconstructed session state.

1. Create or reuse the session shell provider
    - Reuse the detected shell provider and session snapshot metadata.
    - Do not reuse a live shell process.

2. Build the wrapped command string
    - source the session snapshot if it exists
    - source the current session env script if one exists
    - apply shell-safety setup such as disabling dangerous extglob modes
    - `eval` the quoted command string
    - append `pwd -P > cwd_temp_file`

3. Choose login-shell behavior
    - if a snapshot is available, run without `-l`
    - if no snapshot is available, fall back to login-shell startup

4. Spawn the process
    - start from the current session cwd
    - set environment safeguards such as `GIT_EDITOR=true`
    - if Aunic uses a shell marker env var like `CLAUDECODE=1`, set it here
    - if sandboxing is enabled, wrap the command before spawn

5. Capture output
    - stream stdout/stderr into a task-output sink
    - preserve progress chunks for the UI
    - persist large outputs to disk when they exceed the inline budget

6. Update session state after completion
    - if the command was foregrounded and produced a cwd temp file, update the session cwd from it
    - invalidate cached session env script if cwd changes imply hook-driven env changes
    - clean up per-command temp files

## Concurrency
Because Aunic is not using one live shell process, bash does not need a blanket "one command at a time" rule anymore.

Instead:
- read-only commands that pass the read-only validator may be marked concurrency-safe
- commands that may change session state should not be treated as concurrency-safe
- background tasks are independent once spawned

This is much closer to `coding-agent-program-example` than a global single-shell queue.

## Background Execution And Progress
This is one of the biggest improvements to take from the example.

### `run_in_background`
If `run_in_background` is `true`:
- spawn the command
- detach it into Aunic's background-task registry
- return immediately with a normal `tool_result`
- include the background task id and output path metadata

### Foreground progress
For long-running foreground commands:
- start showing progress after a short threshold
- stream recent output and elapsed time into the UI
- make it possible to background the command from the UI if Aunic supports that interaction

### Optional auto-backgrounding
If Aunic wants to follow the example closely in assistant-like modes:
- auto-background long-running blocking commands after a configured budget
- return a normal `tool_result` that tells the model the task is still running in the background

### Sleep-loop steering
Like the example:
- discourage or block leading `sleep N` polling patterns
- encourage background tasks or a dedicated monitor tool instead

## Timeouts And Cancellation
If the command exceeds its timeout, or if the run is cancelled:
- terminate the spawned child process tree
- because Aunic is using fresh shell processes per command, there is no long-lived shell process to preserve
- the result should indicate whether execution timed out, was interrupted, or was backgrounded before completion

A timeout is not the same thing as a validation failure.
- the command did start running
- therefore this should return a `tool_result`, not a `tool_error`

## Formatting The Transcript Result
Unlike `coding-agent-program-example`, Aunic stores durable tool history in the `transcript`, so the canonical stored result should remain a structured JSON object.

Suggested `tool_result` transcript content format:
```json
{
  "command": "pytest -q",
  "description": "Run the pytest test suite",
  "cwd_before": "/repo",
  "cwd_after": "/repo",
  "stdout": "....",
  "stderr": "",
  "exit_code": 0,
  "timed_out": false,
  "interrupted": false,
  "duration_ms": 1834,
  "started_at": "2026-03-31T20:10:00.000Z",
  "completed_at": "2026-03-31T20:10:01.834Z",
  "return_code_interpretation": null,
  "no_output_expected": false,
  "background_task_id": null,
  "backgrounded_by_user": false,
  "assistant_auto_backgrounded": false,
  "used_sandbox": true,
  "sandbox_escalated": false,
  "persisted_output_path": null,
  "persisted_output_size": null,
  "is_image": false,
  "snapshot_used": true
}
```

### Why This Should Stay Structured
- the model can see whether the command actually failed vs merely produced stderr
- the transcript preserves enough context for later runs
- the renderer can show a clean summary without throwing away raw output
- provider translation can still flatten this into a provider-specific tool-result block later

### Long output
If stdout or stderr are too large:
- keep truncated inline fields in the structured `tool_result`
- persist the full output to a tool-results path on disk
- store that path and the size in the structured `tool_result`
- provider translation may turn this into the example-style persisted-output preview text for the model

### Images or other non-text shell output
If Aunic later supports image-like shell output:
- the transcript should still store structured metadata
- provider translation can emit image blocks or image-aware tool-result content

### No output
If both stdout and stderr are empty:
- still return a normal `tool_result`
- keep `stdout` and `stderr` as empty strings
- the UI may render this as "no output", but the stored transcript value should remain the structured JSON object

## Formatting Rejections And Pre-Execution Failures
This is another intentional Aunic-specific retention.

Use `tool_error` when Aunic itself could not or would not execute the command.
- malformed arguments
- unsupported internal schema values
- note-content mutation rejection
- permission rejection
- sandbox escalation rejected before spawn
- shell provider creation failure
- snapshot/bootstrap failure before the command actually ran
- internal execution error before process start

Suggested `tool_error` transcript content format:
```json
{
  "category": "permission_denied",
  "reason": "user_reject",
  "command": "git push origin main",
  "cwd": "/path/to/project",
  "message": "Execution was rejected by the user."
}
```

## What Counts As A Failure
### `tool_error`
Use `tool_error` when Aunic itself could not or would not execute the command.
- malformed arguments
- permission rejection
- shell startup failure
- snapshot/bootstrap failure before spawn
- internal execution error before the command could actually run

### `tool_result`
Use `tool_result` when the command did run, even if the outcome was bad.
- non-zero exit code
- command printed error text to stderr
- timeout after process start
- interrupted execution
- command was backgrounded

This distinction matters because a non-zero shell exit is useful feedback for the model, not a transport-level tool failure.

## Returning The Result To The Model
After the transcript row is built:
1. write the `tool_result` or `tool_error` row to the in-memory message list
2. write the same row to the `transcript`
3. return the result to the provider in the provider-specific tool-result shape described in `notes-and-plans/building-context/transcript-to-api.md`
4. prompt the model for its next step

Important:
- the provider-facing shape may look closer to `coding-agent-program-example`, including flattened text or persisted-output preview text
- the transcript row remains the canonical structured record inside Aunic
- the explicit transcript distinction between `tool_result` and `tool_error` must not be lost just because a provider only offers a single tool-result transport primitive

At that point the model can:
- inspect stdout / stderr
- react to exit codes
- notice background-task ids or persisted-output paths
- decide whether to retry with a different command
- switch to another tool such as `read`, `edit`, `grep`, or a note tool

## Rendering In Aunic
The full transcript rendering rules already live in `notes-and-plans/active-markdown-note/active-markdown-note.md`, but for `bash` the renderer should at minimum surface:
- the command
- the optional description
- the exit code
- whether it timed out / was interrupted / was backgrounded
- the cwd the command ran in
- whether the sandbox was used or escalated
- whether the result is truncated or persisted to disk
- an easy way to expand and inspect stdout / stderr

## Summary Of The Full Flow
1. the model emits a `bash` tool call
2. Aunic parses it and records the `tool_call`
3. local validation runs
4. AST safety and permission checks run
5. if rejected before spawn, return `tool_error`
6. if allowed, build the wrapped command from session snapshot + session env + cwd
7. spawn a fresh shell process for the command
8. capture progress, stdout / stderr / exit code / cwd / timing
9. if the command actually ran, write a structured `tool_result`
10. if Aunic itself failed before execution, write a structured `tool_error`
11. return the provider-facing tool-result representation to the model for the next turn
