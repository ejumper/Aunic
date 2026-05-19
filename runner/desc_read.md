Read a file from the filesystem with line numbers. Does not end the run.

<parameters>
- file_path: Absolute path to the file to read. Required.
- offset: Line number to start reading from (1-based). Defaults to 1 (start of file). Optional.
- limit: Maximum number of lines to read. Defaults to 2000. Optional.
</parameters>

<features>
- Returns content in cat -n format with 1-based line numbers: "     1\tline content"
- Reports total_lines so you know how much was skipped if truncated.
- Use offset + limit to read a specific section of a large file.
</features>

<limitations>
- Default limit: 2000 lines. Set limit explicitly if you need more.
- Cannot read directories — use Bash for directory listings.
- Reading device paths (/dev/zero, /dev/random, /dev/urandom, etc.) is blocked.
</limitations>

<tips>
- Read the file before using Edit — you need the exact content to construct old_string.
- For large files: use Grep first to find the relevant line range, then Read with offset + limit.
- Use total_lines from the result to determine whether important content was cut off.
</tips>
