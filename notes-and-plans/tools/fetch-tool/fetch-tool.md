## The Fetch Tool
The model fetches web pages using a `web_fetch` tool. The model provides a URL, Aunic fetches the page, converts it to markdown, and returns the full content to the model for the current run. The transcript stores only a compact summary — the full page lives in the filesystem cache.
- `web_fetch`
    - `url`
        - Required string. The URL to fetch.
        - This is the only parameter. The tool follows the standard pattern models are trained on.

## Fetch Execution
When the model calls `web_fetch`, Aunic runs the following sequence:
1. **Check the page cache**
    - Fetched pages are cached on the filesystem per active markdown file (see Page Caching below).
    - The URL is canonicalized and looked up in the cache. If present, the cached markdown is used — no HTTP request is made.
    - If not cached, the page is fetched via HTTP.
2. **Fetch the page via HTTP**
    - Uses `httpx` with `follow_redirects=True`.
    - Request timeout is controlled by `fetch_request_timeout_seconds` in settings.
    - User-Agent header is set from settings.
    - If the fetch fails (HTTP error, timeout, etc.), a tool error is returned.
3. **Convert HTML to markdown**
    - HTML pages are converted to markdown using `trafilatura` (with links and formatting preserved).
    - Non-HTML responses (plain text, etc.) are used as-is.
    - If conversion produces no readable content, a tool error is returned.
4. **Cache the result**
    - The full markdown text and metadata are written to the filesystem cache.
    - Both the requested canonical URL and the resolved canonical URL (in case of redirects) are recorded in the manifest so either can produce a cache hit on future calls.
5. **Return the result**
    - The **in-memory message list** receives the full page markdown (truncated at `fetch_max_chars`, e.g., 100K chars, with a note that the page was truncated if applicable). The model can read and reason over the full content for the duration of this run.
    - The **transcript** receives a compact summary: `{"url":"...","title":"...","snippet":"..."}`. This keeps the transcript small so future runs don't bloat the context window with old page content.
    - This means `web_fetch` is a **split-persistence** tool: the in-memory result differs from the transcript result. This is the same pattern as thinking blocks (present during the run, not persisted in full).

## Split Persistence
Unlike most persistent tools where the transcript stores exactly what the model saw, `web_fetch` splits what goes where:
- **In-memory** (what the model sees during this run): full page markdown, up to `fetch_max_chars`.
- **Transcript** (what persists across runs): compact JSON with URL, title, and snippet. Enough for the model to see "I fetched this page and it was about X" on future runs.
- **Filesystem cache**: full page markdown. If the model needs the content again on a future run, it calls `web_fetch` again — instant from cache, no HTTP.

This prevents a common agent problem: every fetched page permanently inflating the context window on all future runs. The model gets full content when it needs it, compact history when it doesn't.

## Page Caching
Fetched pages are stored on the filesystem using the XDG cache convention. The cache is fully disposable — deleting it just means pages get re-fetched on the next call.

### Cache location
```
~/.cache/aunic/fetch/<note-path-hash>/
    <url-hash>.md           # the converted markdown text
    <url-hash>.meta.json    # per-entry metadata
    manifest.json           # index of all entries for this note
```
- `$XDG_CACHE_HOME/aunic/fetch/` is used if `$XDG_CACHE_HOME` is set, otherwise `~/.cache/aunic/fetch/`.
- `<note-path-hash>` is a hash of the absolute path to the active markdown file. Each note gets its own isolated cache namespace.
- `<url-hash>` is a hash of the canonical URL.

### What is stored
- The `.md` file contains the full converted markdown text.
- The `.meta.json` file contains per-entry metadata:
    ```json
    {
        "canonical_url": "https://example.com/page",
        "title": "Page Title",
        "original_url": "https://example.com/page?ref=123",
        "fetched_at": "2026-03-31T14:22:00Z"
    }
    ```
- If a redirect resolves to a different canonical URL, the manifest records both URLs pointing to the same `<url-hash>` entry so either produces a cache hit.

### Manifest
The `manifest.json` file tracks all entries and their sizes for fast eviction decisions:
```json
{
    "entries": {
        "<url-hash>": {
            "canonical_url": "https://example.com/page",
            "title": "Page Title",
            "size_bytes": 24500,
            "fetched_at": "2026-03-31T14:22:00Z",
            "last_accessed": "2026-03-31T15:10:00Z"
        }
    },
    "aliases": {
        "<original-url-hash>": "<resolved-url-hash>"
    },
    "total_size_bytes": 1240000
}
```
- `last_accessed` is updated on every cache read (not just writes), so frequently referenced pages stay warm.
- `aliases` maps redirect source URL hashes to their resolved URL hash entries.

### Eviction
- Per-note size cap: **3MB** (configurable in settings as `fetch_cache_max_bytes`).
- When a new fetch would push `total_size_bytes` over the cap, entries are evicted **LRU** (least recently accessed first) until there is room.
- No expiration dates — pages are kept until evicted by the size cap or the user manually deletes the cache directory.

### In-memory read-through
During a run, `FetchService` may hold the most recently accessed cached page in memory to avoid repeated disk reads within the same run. This in-memory reference is discarded when the run ends — the filesystem cache is the source of truth.

## Rendering in the Transcript
The tool_call and tool_result rows sharing the same `tool_id` are rendered together as a single row:
- The row shows the page title, a snippet, and a "↗" button to open the URL in the default browser.
- If the fetched page is present in the filesystem cache, the page title is underlined and blue — clicking it copies the cached markdown to the clipboard.
- Clicking the snippet copies it to the clipboard.
- (see `notes-and-plans/active-markdown-note/active-markdown-note.md` for the full rendering spec)

## Error Handling
- HTTP errors, timeouts, and connection failures return a `tool_error` result to the model.
- If HTML-to-markdown conversion produces no readable content, a `tool_error` result is returned.
- The model can retry with a different URL or move on.

## Relationship to `web_search`
- `web_fetch` typically follows a `web_search` call. The model uses URLs from search results to decide which pages to fetch.
- `web_fetch` can also be called with any URL, independent of any prior search.

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)
