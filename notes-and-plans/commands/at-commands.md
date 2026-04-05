# user search and fetch
@web
- allows the user to harness the optimized search tools to quickly find information without going to the browser
1. the user types @web <search query> and hits send
    - (note: needs protection where if @web has no text after it, an error appears in indicator-area)
2. the search tool runs and returns the filtered/scored search results replacing the `prompt_editor_box` 
    - (note: keep the bottom row buttons, but gray out everything but the send button)
    - search results appear "rendered" like this...
       "-[ ] <page title> (cut off with "..." to keep it on one line, and the checkbox rendered)
             <url>" (also cutoff to stay on one line)
    - the user can navigate the results with up and down arrows
    - left and right arrows expand and contract the results to show/hide the website snippet beneath the url
    - when they click on the url (or hit enter with the search result in focus) it should open in the browser
3. the user selects a single checkbox and hits send (space selects the checkbox, ctrl+r to send it)
4. that page is fetched, using the fetch tool and its converted to markdown.
5. The search results list is replaced with a parsed/chunked view of the fetched page. each chunk has a checkbox. 
6. the user can select one or more chunks (space) and hit send (crtl+r).
    - they can also hit escape to return to the search results
7. the selected chunks are then appended to the bottom of the note/chat area, or just above the non-work-log/search-results (however is best to frame it), with the page title as h1 at the top.
- ctrl+c to cancel the search at any stage

## @web chunking
The chunk-and-score pipeline is used **only by the @web user flow**, not by the model's `web_fetch` tool. When the model calls `web_fetch`, it gets the full page content — no chunking (see `notes-and-plans/tools/fetch-tool/fetch-tool.md`).

For @web, chunking is necessary because the user is manually selecting which parts of a page to pull into the note. Showing the full page as a wall of text would be unusable — the user needs it broken into scannable, selectable pieces.

### How chunking works for @web
1. The fetched markdown is split into chunks using `chunk_markdown_text()`.
    - Chunk size is controlled by `fetch_chunk_target_chars` and `fetch_chunk_hard_cap_chars` in settings.
2. Obvious junk chunks are filtered out (headings-only, duplicates, very short fragments).
3. Each surviving chunk is scored using a weighted combination of:
    - **Semantic score**: cosine similarity between the user's search query embedding and the chunk embedding (via Ollama `mxbai-embed-large`).
    - **Lexical score**: term overlap between the search query and the chunk text (with spaCy lemmatization when available, fallback to basic tokenization).
    - **Heading score**: term overlap between the search query and the chunk's heading path.
4. Score weights are configurable: `semantic_score_weight`, `lexical_score_weight`, `heading_score_weight`.
5. Chunks are sorted by score (highest first) and presented to the user in step 5 above.
    - All chunks are shown (not pruned by score) — the user decides what's relevant, the score just determines the display order.
    - Each chunk shows its heading path as context so the user knows where on the page it came from.
## Adding @web results to the transcript
When a user completes a search or fetch via `@web`, the results are written to the `transcript` table the same way model-initiated tool calls are — as `tool_call` and `tool_result` row pairs. The only difference is that Aunic generates the rows rather than receiving them from an API response.
- `tool_id`: synthetic, using a `user_` prefix plus an incrementing counter scoped to the transcript (e.g., `user_001`, `user_002`). This avoids collisions with API-generated IDs (`toolu_...` for Anthropic, `call_...` for OpenAI).
- `role`: `tool_call` rows use `assistant`, `tool_result` rows use `tool`. This matches the structure the model expects to see in its history — a tool was called and a result was returned.
- `web_search` example:
    ```
    | 7 | assistant | tool_call   | web_search | user_001 | {"queries":["search query"]}
    | 8 | tool      | tool_result | web_search | user_001 | [{"url":"...","title":"...","snippet":"..."}]
    ```
- `web_fetch` example (if the user selects a page to fetch):
    ```
    | 9  | assistant | tool_call   | web_fetch | user_002 | {"url":"https://example.com"}
    | 10 | tool      | tool_result | web_fetch | user_002 | "fetched page snippet or summary"
    ```
- These rows are indistinguishable from model-initiated searches when translated to the API. On future runs, the model sees them as normal tool history and can reference the results.

# temporarily add file
@path/to/file.md
- temporarily adds additional files to the context window for that prompt send.
(note: later feature)

# temporarily exclude file
%path/to/file.md
- temporarily excludes files that have been added via /include for the next prompt
- if a file not in /include list is added, an error should display