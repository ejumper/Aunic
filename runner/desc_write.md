Write content to a file, creating or overwriting it. Does not end the run.

<parameters>
- file_path: Absolute path to the file to write. Required.
- content: The full content to write to the file. Required.
</parameters>

<features>
- Creates parent directories automatically if they do not exist.
- Overwrites the file completely if it already exists — no confirmation.
</features>

<when_to_use>
Prefer Edit for targeted changes to an existing file. Use Write when:
- Creating a new file.
- Completely rewriting a file from scratch.
- Making many scattered changes where multiple Edit calls would be unwieldy.
</when_to_use>

<warnings>
Read the file first if it already exists. Write replaces the entire contents — any section you omit is lost.
</warnings>
