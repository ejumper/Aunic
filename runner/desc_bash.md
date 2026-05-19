Execute shell commands on the local filesystem.

<execution_notes>
- Use Grep/Glob tools instead of 'find'/'grep'. Use Read/Write/Edit tools instead of 'cat'/'echo'/'head'/'tail'.
- Each command runs in an independent shell — no state persists between calls.
- Prefer absolute paths; avoid cd unless necessary.
- Commands exceeding auto_background_after seconds (default %d) automatically move to background.
- Output is capped at %d characters; large output is trimmed from the middle.
</execution_notes>

<banned_commands>
The following commands are blocked for security: %s.
Package manager installs are also blocked (brew install, npm install -g, pip install --user, etc.).
</banned_commands>

<background_execution>
- Set run_in_background=true for long-running processes (servers, watchers).
- Commands that take longer than auto_background_after seconds automatically background themselves.
- Use job_output to view background output; job_kill to terminate.
- NEVER use &amp; at the end of a command — use run_in_background instead.
</background_execution>
