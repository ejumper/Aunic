Execute shell commands on the local filesystem. Does not end the run.

<parameters>
- command: The shell command to execute. Required.
- description: Brief label for logging and background tracking (30 chars or less). Optional but recommended.
- working_dir: Absolute path to run the command in. Defaults to the active note's directory. Optional.
- run_in_background: If true, start the command immediately in background and return. Optional.
- auto_background_after: Seconds before auto-backgrounding a slow synchronous command. Default %d. Optional.
</parameters>

<execution_notes>
- Each command runs in an independent shell — no state (env vars, directory changes) persists between calls.
- Prefer absolute paths; avoid cd unless necessary.
- Use Grep/Glob tools instead of shell grep/find commands.
- Use Read/Write/Edit tools instead of cat/echo/head/tail/sed/awk.
- Output is capped at %d characters; large output is trimmed from the middle.
</execution_notes>

<background_execution>
Background mode is for fire-and-forget long-running processes (servers, build watchers) where you don't need the output before continuing.

- Set run_in_background=true to start immediately in background.
- Synchronous commands exceeding auto_background_after seconds are automatically backgrounded.
- NEVER use & at the end of a command — use run_in_background instead.
- Once backgrounded, there is no tool to query the job's output or stop it.
</background_execution>

<banned_commands>
The following commands are blocked: %s.
Package manager installs are also blocked (brew install, npm install -g, pip install --user, go install, etc.).
</banned_commands>
