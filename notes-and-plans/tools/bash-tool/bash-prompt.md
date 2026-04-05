## Bash Tool Prompt Outline
This file outlines how Aunic should structure the system prompt for the `bash` tool.
- explain what the tool does
- steer the model toward dedicated tools whenever possible
- define operational rules for command construction
- explain sandbox behavior
- optionally inject git-specific guidance

## Top-Level Prompt Shape
The prompt should be assembled in this general order:

1. short tool description
2. shell-state / working-directory explanation
3. strong preference for dedicated tools over shell commands
4. an `Instructions` section with operational rules
5. an optional `Command sandbox` section
6. an optional git / PR workflow section

That overall structure is one of the strongest parts of the reference implementation.
- the model is told what the tool is
- then told when not to use it
- then given concrete rules for using it safely
- then given environment-specific constraints
- then given specialized git behavior only when relevant

## Section 1: Tool Description
Start with a short plain-language description.

Reference prompt text:

```text
Executes a given bash command and returns its output.
```

This section should stay simple.
- no long policy text here
- just define the tool's basic purpose

## Section 2: Shell State / Working Directory
Immediately after the description, explain how command execution state behaves.

Reference prompt text:

```text
The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).
```

For Aunic, this section should match the final bash implementation.
- Aunic keeps the example's reconstructed-per-command shell model, say that explicitly

Suggested shape:
- explain whether `cwd` persists
- explain whether aliases, exports, functions, and shell-local state persist
- explain whether the shell is initialized from profile files

## Section 3: Prefer Dedicated Tools
This is one of the most important sections in the reference prompt.

Reference prompt text:

```text
IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:

- File search: Use GlobTool (NOT find or ls)
- Content search: Use GrepTool (NOT grep or rg)
- Read files: Use ReadTool (NOT cat/head/tail)
- Edit files: Use EditTool (NOT sed/awk)
- Write files: Use WriteTool (NOT echo >/cat <<EOF)
- Communication: Output text directly (NOT echo/printf)
```

If Aunic conditionally removes embedded search tools, mirror the reference builder's conditional variant too:

```text
IMPORTANT: Avoid using this tool to run `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:

- Read files: Use ReadTool (NOT cat/head/tail)
- Edit files: Use EditTool (NOT sed/awk)
- Write files: Use WriteTool (NOT echo >/cat <<EOF)
- Communication: Output text directly (NOT echo/printf)
```

For Aunic, swap in Aunic's real tool names at render time rather than hardcoding the example names.

For Aunic, that list should reflect which embedded tools actually exist in the active mode.

## Section 4: Why Dedicated Tools Are Better
After the tool-preference mapping, add a short explanation for why.

Reference prompt text:

```text
While the Bash tool can do similar things, it’s better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.
```

Aunic should keep this idea.
- it helps smaller models understand that this is not just a suggestion
- it frames the choice in terms of user experience and auditability

## Section 5: `Instructions`
This is the main operational rules section.
In the reference implementation, this is a large bullet list with several sub-groups.

### 5a. File / Path Safety Rules
Reference prompt text:

```text
- If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.
- Always quote file paths that contain spaces with double quotes in your command (e.g., cd "path with spaces/file.txt")
- Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.
```

This section should reflect Aunic's final shell behavior.
- if Aunic reconstructs shell state per command, strongly prefer absolute paths
- if Aunic keeps persistent `cwd`, the prompt can be slightly looser, but absolute paths are still safer

### 5b. Timeout Rules
Reference prompt text:

```text
- You may specify an optional timeout in milliseconds (up to ${getMaxTimeoutMs()}ms / ${getMaxTimeoutMs() / 60000} minutes). By default, your command will timeout after ${getDefaultTimeoutMs()}ms (${getDefaultTimeoutMs() / 60000} minutes).
```

Aunic should mirror that structure:
- explain that timeout can be specified
- state the default timeout
- state the maximum timeout

### 5c. Background Execution Rules
Reference prompt text:

```text
You can use the `run_in_background` parameter to run the command in the background. Only use this if you don't need the result immediately and are OK being notified when the command completes later. You do not need to check the output right away - you'll be notified when it finishes. You do not need to use '&' at the end of the command when using this parameter.
```

If Aunic supports background execution, this should be included.
If not, omit it entirely.

### 5d. Multiple Commands Rules
This is a very useful structure from the reference prompt.

Reference prompt text:

```text
- If the commands are independent and can run in parallel, make multiple Bash tool calls in a single message. Example: if you need to run "git status" and "git diff", send a single message with two Bash tool calls in parallel.
- If the commands depend on each other and must run sequentially, use a single Bash call with '&&' to chain them together.
- Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.
- DO NOT use newlines to separate commands (newlines are ok in quoted strings).
```

This section is worth keeping almost exactly in spirit.

### 5e. Git Command Rules
Reference prompt text:

```text
- Prefer to create a new commit rather than amending an existing commit.
- Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.
- Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.
```

Aunic should keep a small git safety subsection here even if larger git instructions are injected separately.

### 5f. Avoid `sleep`
Reference prompt text when the monitor-style path is available:

```text
- Do not sleep between commands that can run immediately — just run them.
- Use the Monitor tool to stream events from a background process (each stdout line is a notification). For one-shot "wait until done," use Bash with run_in_background instead.
- If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed.
- Do not retry failing commands in a sleep loop — diagnose the root cause.
- If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.
- `sleep N` as the first command with N ≥ 2 is blocked. If you need a delay (rate limiting, deliberate pacing), keep it under 2 seconds.
```

Reference prompt text when that monitor path is not available:

```text
- Do not sleep between commands that can run immediately — just run them.
- If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed.
- Do not retry failing commands in a sleep loop — diagnose the root cause.
- If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.
- If you must poll an external process, use a check command (e.g. `gh run view`) rather than sleeping first.
- If you must sleep, keep the duration short (1-5 seconds) to avoid blocking the user.
```

This is a good optimization for smaller models especially.
- it reduces lazy polling behavior
- it cuts down on wasted tool calls

### 5g. Environment-Specific Caveats
The reference prompt adds special caveats conditionally, for example around `find -regex` behavior in the embedded-search environment.

Reference prompt text:

```text
When using `find -regex` with alternation, put the longest alternative first. Example: use `'.*\\.\\(tsx\\|ts\\)'` not `'.*\\.\\(ts\\|tsx\\)'` — the second form silently skips `.tsx` files.
```

For Aunic, this should be an optional injection point.
- only include these if there is a real shell/environment quirk the model needs to know
- keep them narrow and concrete

## Section 6: Command Sandbox
The reference implementation conditionally appends a `Command sandbox` section.
This is one of the most detailed parts of the prompt.

Its job is to explain:
- that commands run in a sandbox by default
- what filesystem restrictions exist
- what network restrictions exist
- when sandbox override is allowed
- how temporary files should be handled

### 6a. Explain That Sandboxing Exists
Reference prompt text:

```text
## Command sandbox
By default, your command will be run in a sandbox. This sandbox controls which directories and network hosts commands may access or modify without an explicit override.
```

### 6b. Show Effective Restrictions
Reference prompt text:

```text
The sandbox has the following restrictions:
Filesystem: ...
Network: ...
Ignored violations: ...
```

For Aunic, this section should be generated from actual runtime state.
- do not hardcode it in the prompt text
- inject the current effective restrictions

### 6c. Explain Sandbox Override Policy
Reference prompt text when unsandboxed retry is allowed:

```text
- You should always default to running commands within the sandbox. Do NOT attempt to set `dangerouslyDisableSandbox: true` unless:
  - The user *explicitly* asks you to bypass sandbox
  - A specific command just failed and you see evidence of sandbox restrictions causing the failure. Note that commands can fail for many reasons unrelated to the sandbox (missing files, wrong arguments, network issues, etc.).
- Evidence of sandbox-caused failures includes:
  - "Operation not permitted" errors for file/network operations
  - Access denied to specific paths outside allowed directories
  - Network connection failures to non-whitelisted hosts
  - Unix socket connection errors
- When you see evidence of sandbox-caused failure:
  - Immediately retry with `dangerouslyDisableSandbox: true` (don't ask, just do it)
  - Briefly explain what sandbox restriction likely caused the failure. Be sure to mention that the user can use the `/sandbox` command to manage restrictions.
  - This will prompt the user for permission
- Treat each command you execute with `dangerouslyDisableSandbox: true` individually. Even if you have recently run a command with this setting, you should default to running future commands within the sandbox.
- Do not suggest adding sensitive paths like ~/.bashrc, ~/.zshrc, ~/.ssh/*, or credential files to the sandbox allowlist.
```

Reference prompt text when unsandboxed retry is disabled:

```text
- All commands MUST run in sandbox mode - the `dangerouslyDisableSandbox` parameter is disabled by policy.
- Commands cannot run outside the sandbox under any circumstances.
- If a command fails due to sandbox restrictions, work with the user to adjust sandbox settings instead.
```

If Aunic supports sandbox escalation, the prompt should explain:
- default to sandboxed execution
- only use override when there is evidence the sandbox caused the failure
- evidence examples: permission denied, blocked path, blocked host, socket denial
- retry with override only when the failure really points to sandbox restrictions

If Aunic does not support sandbox override in a given mode, say so explicitly.

### 6d. Temporary File Rules
Reference prompt text:

```text
- For temporary files, always use the `$TMPDIR` environment variable. TMPDIR is automatically set to the correct sandbox-writable directory in sandbox mode. Do NOT use `/tmp` directly - use `$TMPDIR` instead.
```

If Aunic has a preferred scratch/temp directory, this section should state it clearly.

## Section 7: Commit / PR Workflow Injection
The reference prompt optionally appends a large git section.
This is not part of the basic bash prompt all the time. It is injected only when git instructions are enabled.

That section has two layers:
- a lightweight version for internal users pointing to skills
- a full inline workflow for external users

For Aunic, the important pattern is:
- keep advanced git workflow instructions in a separate optional section
- do not always include them if they are not relevant

### 7a. Commit Instructions
Reference prompt text for the short internal branch:

```text
For git commits and pull requests, use the `/commit` and `/commit-push-pr` skills:
- `/commit` - Create a git commit with staged changes
- `/commit-push-pr` - Commit, push, and create a pull request

These skills handle git safety protocols, proper commit message formatting, and PR creation.

Before creating a pull request, run `/simplify` to review your changes, then test end-to-end (e.g. via `/tmux` for interactive features).
```

Reference prompt text for the full external commit branch:

```text
# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

You can call multiple tools in a single response. When multiple independent pieces of information are requested and all commands are likely to succeed, run multiple tool calls in parallel for optimal performance. The numbered steps below indicate which commands should be batched in parallel.

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive git commands (push --force, reset --hard, checkout ., restore ., clean -f, branch -D) unless the user explicitly requests these actions. Taking unauthorized destructive actions is unhelpful and can result in lost work, so it's best to ONLY run these commands when given direct instructions
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests a git amend. When a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit, which may result in destroying work or losing previous changes. Instead, after hook failure, fix the issue, re-stage, and create a NEW commit
- When staging files, prefer adding specific files by name rather than using "git add -A" or "git add .", which can accidentally include sensitive files (.env, credentials) or large binaries
- NEVER commit changes unless the user explicitly asks you to. It is VERY IMPORTANT to only commit when explicitly asked, otherwise the user will feel that you are being too proactive
```

If Aunic wants explicit commit/PR bash guidance, this is a good model.

### 7b. Pull Request Instructions
Reference prompt text for the full external PR branch:

```text
# Creating pull requests
Use the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request, follow these steps carefully:

1. Run the following bash commands in parallel using the Bash tool, in order to understand the current state of the branch since it diverged from the main branch:
   - Run a git status command to see all untracked files (never use -uall flag)
   - Run a git diff command to see both staged and unstaged changes that will be committed
   - Check if the current branch tracks a remote branch and is up to date with the remote, so you know if you need to push to the remote
   - Run a git log command and `git diff [base-branch]...HEAD` to understand the full commit history for the current branch (from the time it diverged from the base branch)
2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request title and summary:
   - Keep the PR title short (under 70 characters)
   - Use the description/body for details, not the title
3. Run the following commands in parallel:
   - Create new branch if needed
   - Push to remote with -u flag if needed
   - Create PR using gh pr create with the format below. Use a HEREDOC to pass the body to ensure correct formatting.
```

Reference prompt text for the closing reminders:

```text
Important:
- DO NOT use the TodoWriteTool or AgentTool tools
- Return the PR URL when you're done, so the user can see it

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments
```

Again, this should be optional and separate from the base prompt.

## Prompt Assembly Strategy
So the final prompt builder for Aunic should probably follow this assembly pattern:

1. base one-line description
2. shell-state / cwd behavior
3. strong preference for dedicated tools
4. short explanation of why those tools are better
5. `Instructions`
6. optional `Command sandbox`
7. optional advanced git / PR section

## Dynamic Inputs The Prompt Should Pull From Runtime
Like the reference implementation, this prompt should not be fully static.

It should be able to inject:
- actual tool names available in the current mode
- whether embedded search tools exist
- default and max timeout values
- whether background tasks are enabled
- actual sandbox restrictions
- whether sandbox override is allowed
- whether commit / PR instructions should be included
- any environment-specific warning text

## Aunic-Specific Notes
When adapting this outline to Aunic, the most important divergence is:
- the prompt must reflect Aunic's real bash implementation, not the reference app's assumptions

In particular:
- if shell state does not persist, say that clearly
- if transcript persistence matters for tool review, mention that in the "why dedicated tools are better" logic
- if Aunic bans or restricts certain shell behaviors more aggressively than the reference app, the prompt should state that directly

## Recommended Tone
The reference prompt works well because it is:
- direct
- specific
- operational
- full of concrete examples

Aunic's version should preserve that style.
- avoid abstract philosophy
- prefer explicit do/don't guidance
- use concrete examples where a rule is easy to misunderstand
