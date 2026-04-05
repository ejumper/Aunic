# The Active Markdown Note Formatting
The `active-markdown-note` is the file that the user is currently prompting from.
- It is stored as a markdown file on the system.
- The file is parsed by Aunic into 2 sections: `note-content` and `transcript`
    - `note-content`
        - `note-content` appears at the top of the file above the `transcript`
        - It is made up of plain text, usually markdown formatted (although it doesn't have to be) it is either written by the user or the LLM and is what is being created/edited in `note-mode`
        - see notes-and-plans/modes/modes.md for more details
    - `transcript`
        - the `transcript` is a table shown below the TextArea containing a chronological view of the `chat-mode` messages, the `agentic-tool-use` and the `search-results`
            - (note: `agentic-tool-use` is any tool used by work mode (see note-and-plans/modes/modes.md for more details, `search-results` are `web_search` and `web_fetch` tool use from either chat or note mode))
        - it is stored in the same markdown file as `note-content` as a markdown table, but Aunic renders it separately from the `note-content` in the `text-editor` TextArea, delineated by "---\n# Transcript" in the file
        - the table delineates `note-content`, by row formatting/content. 
        - note: the rendered table in Aunic will look different then the actual markdown table. The markdown table's primary purpose is to allow the API to rebuild the message history, and the Aunic rendering's purpose is to be easily readable by humans.
        - **Transcript initialization**
            - The `---\n# Transcript` delimiter and column header row do not exist in a new note. They are created on the first action that requires the transcript (first chat message sent, first tool call recorded, first @web search, etc.).
            - When created, Aunic appends the following to the end of the file:
                ```
                ---
                # Transcript
                | # | role      | type        | tool_name  | tool_id  | content
                |---|-----------|-------------|------------|----------|-------------------------------
                ```
            - The first data row is then appended immediately after.
            - If the file has no transcript section, Aunic treats it as having zero transcript rows — the parser returns an empty list, and the transcript UI area is empty.
        - **Transcript repair**
            - If the `---\n# Transcript` delimiter or `# Transcript` header is damaged (e.g., user accidentally edited it) but table rows matching the expected column pattern (`| <#> | <role> | ...`) are still present in the file, Aunic should attempt to repair rather than create a duplicate transcript section.
            - Repair logic: scan the file bottom-up for lines matching the transcript row pattern. If found, reconstruct the delimiter and header row immediately above the first matched table row. This avoids ending up with two transcript sections in one file.
            - If no table rows are found anywhere in the file, treat it as having no transcript and create a fresh one on the next action that requires it.
        - The actual markdown table should look like...
            | # | role      | type        | tool_name  | tool_id  | content
            |---|-----------|-------------|------------|----------|-------------------------------
            | 1 | user      | message     |            |          | "What's the weather?"
            | 2 | assistant | tool_call   | web_search | call_01  | {"queries":["weather today"]}
            | 3 | tool      | tool_result | web_search | call_01  | [{"url":"https://example.com","title":"Weather","snippet":"72°F and sunny"}]
            | 4 | assistant | message     |            |          | "The weather is 72°F and sunny."
        - **Content cell encoding**: The `content` column always contains a valid JSON value. This avoids multiline and special character issues in the markdown table.
            - Text messages (user or assistant) are stored as JSON strings: `"What's the weather?"`
                - This means the value is wrapped in double quotes with standard JSON escaping for newlines (`\n`), tabs (`\t`), quotes (`\"`), backslashes (`\\`), etc.
            - Tool call arguments are stored as JSON objects: `{"queries":["weather today"]}`
            - Tool results are stored as JSON — either a JSON string for simple results or a JSON array/object for structured results like search results: `[{"url":"...","title":"...","snippet":"..."}]`
        - **Parsing the content cell**: Because `content` is always the last column, the parser splits the row on `|` and takes everything after the 6th delimiter as the raw content value. This means `|` characters inside JSON strings do not cause mis-splits. The raw content value is then parsed with `json.loads()` / `JSON.parse()` to get the actual value.
        - The "in Aunic" rendering should look like
            - `chat-transcript`
                - All chat messages are in 2 column rows
                    - assistant messages are placed in the left row with the right row empty
                        - left column is 67% of the width right column is 33% of the width
                        - black border around left cell, no border around right cell
                        - height grows to fit wrapped text
                    - user messages are placed in the right row with the left row empty
                        - right column is 67% of the width left column is 33% of the width
                        - black border around right cell, no border around left cell
                        - height grows to fit the wrapped text
            - `agentic-tool-calls`
                - The markdown table has separate rows for `tool_call` and `tool_result`, but only the `tool_result` row is rendered in Aunic. The `tool_call` row is hidden — the user cares about what happened, not what the model asked for.
                - (reminder: only `agentic-tool-calls` are recorded in `transcript`, meaning the set of tools belonging to `work-mode` (see notes-and-plans/modes/work-mode.md). The `edit` tool belonging to `note-mode` is not recorded in the transcript, and the `web-search` and `web-fetch` tool belonging to both `chat-mode` and `note-mode` is recorded in a separate format)
                - **Default tool rendering** (edit, write, read, grep, glob, etc.)
                    - a 2 column row, left column has the tool_name, right column has the tool_result content.
                - **`bash` tool rendering**
                    - Uses the same collapsed/expanded toggle pattern as search results.
                    - **Collapsed state** (default): shows `bash` on the left, the command on the right (first line only, truncated with `...` if long), and a `v` toggle button.
                        |----------------------------------------------|
                        | bash | $ ls -la src/                      | v |
                        |----------------------------------------------|
                    - **Expanded state**: the output from the tool_result is shown below the command row.
                        |----------------------------------------------|
                        | bash | $ ls -la src/                      | v |
                        |----------------------------------------------|
                        | drwxr-xr-x  5 user group  160 Mar 30 ...    |
                        | -rw-r--r--  1 user group 1240 Mar 30 ...    |
                        | -rw-r--r--  1 user group  890 Mar 29 ...    |
                        |----------------------------------------------|
                    - The command string is pulled from the hidden `tool_call` row (matched by `tool_id`), specifically the `command` field of the arguments object.
                    - If the command is multi-line (a script), only the first line is shown in the collapsed row, with `...` appended.
                    - If the output is very long, cap the rendered preview at ~20-30 lines with a scrollable region or "show all" option. The full output remains in the markdown table — this is only a rendering cap.
                    - If the result is a `tool_error`, the output uses a red/error color when expanded.
            - `search-results`
                - (note: applies to search results gotten via a model `tool-call` or the user via the `user-search-and-fetch` "@" command)
                - `search-results` are also rendered with a combined tool_call and tool_results, but with togglable drop down rows like this...
                    - row shows the `tool_call` query, with the number of search results in the `tool_response` with the matching `tool_id`, when the dropdown button on the right is selected, the search results in the `tool_response` are shown each on its own row.
                    |----------------------------------------------|
                    | 3 | <query>                              | v | 
                    |----------------------------------------------|
                    | 1 | <page title 1> | <snippet>           | ↗ |
                    | 2 | <page title 2> | <snippet>           | ↗ |
                    | 3 | <page title 3> | <snippet>           | ↗ |
                    |----------------------------------------------|
                    - the 3 to the left of the query in this example represents the number or search results associated with that query
                    - clicking the "v" shows/hides the search results rows
                    - clicking on any snippet should copy the snippet to the clipboard and clicking the "↗" should open the url in the default browser
                    - if a url matches the url for a `fetch-result` in the table, and that `fetched-page` is present in the metadata/cache associated with the `active-markdown-note`, that row's page title is underlined and uses the blue color and when clicked copies the fetched text to the clipboard.
                - (note: this is a bit more then the other types of transcript entries, so please let me know if anything here isn't doable in prompt_toolkit, or needs a workaround, and we can discuss it further)
            - `fetch-results`
                - (note: applies to fetch results gotten via a model `tool-call` or the user via the `user-search-and-fetch` "@" command)
                - The full fetched page content is only present in the in-memory message list during the run that fetched it. The transcript stores a compact summary: `{"url":"...","title":"...","snippet":"..."}`. The full page is persisted in the filesystem cache (see `notes-and-plans/tools/fetch-tool/fetch-tool.md`).
                - `fetch-calls` and `fetch-results` are rendered as a single row.
                    - one column with the page title, a second with the snippet, a third with "↗" to open the URL in the default browser
                    - if the fetched page is present in the filesystem cache, the page title is underlined and blue — clicking it copies the cached markdown to the clipboard
                    - clicking on any snippet copies it to the clipboard
            - [ Chat ], [ Tools ], [ Search ] that filter all but the specified entries and a [ Descending ]/[ Ascending ] toggle button to reverse the order.
                - the Chat filter shows only the type = message rows
                - the Tools filter shows only the type = tool_call or tool_result rows
                - the Search filter shows only the type = tool_call or tool_result AND tool_name = web_search or tool_name = web_fetch rows
                - Descending of course shows oldest on top to newest on bottom
                - Ascending of course shows newest on top and oldest on bottom
            - **Row deletion**
                - Each rendered row has an "X" on the far left side. Clicking it deletes that entry from the transcript.
                - For tool entries, deletion is cascading: deleting a rendered `tool_result` row also deletes the matching `tool_call` row (matched by `tool_id`) from the markdown table. Since `tool_call` rows are not rendered, the user never needs to delete them manually.
                - For chat messages (`type: message`), deletion removes just that single row.
                - For search/fetch combined rows, deletion removes both the `tool_call` and `tool_result` rows for that `tool_id`.
        - (note: my guess is build it with RICH, and show it with prompt_toolkit, but advice on the best way to make this work is needed)

## Runtime Object
In addition to the markdown file on disk, Aunic should create a runtime `active_markdown_note` object for the current session.

That runtime object should be the source of truth for note-scope enforcement and prompt injection.
- it should include the normalized absolute path to the active note file
- it should include the protected note scope for the run, for example `note_scope_paths`
- today, `note_scope_paths` will usually just be the active note file itself
- later, when note-mode can span multiple included files, `note_scope_paths` can expand to include those files as well

This runtime object should be used by `work-mode` tools like `edit` and `write` to auto-reject attempts to modify the protected note scope, and it should also be used to inject the protected path(s) into relevant system prompts.
