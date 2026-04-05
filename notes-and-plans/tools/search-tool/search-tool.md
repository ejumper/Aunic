## The Search Tool
The model searches the web using a `web_search` tool backed by a self-hosted SearXNG instance. The model provides `queries`, Aunic executes the search, ranks the results, and returns them as a tool result in the transcript.
- `web_search`
    - `queries`
        - An array of search query strings.
        - Currently limited to 1 query per call (maxItems: 1).

## Search Execution
When the model calls `web_search`, Aunic runs the following sequence:
1. **Query is scheduled through the SearXNG Scheduler**
    - The scheduler picks an available search engine from the preferred list: duckduckgo, brave, bing, yahoo, startpage, google, qwant.
    - Per-engine cooldown of 5 seconds prevents hammering the same engine.
    - If an engine fails (returns 0 results), it's blacklisted for 1 hour.
    - If the chosen engine returns no results, the scheduler retries with the next available engine until results are found or all engines are exhausted.
    - When the search starts, Aunic should emit a progress update for the `indicator-area`, for example: `searching <query>...`
2. **Query is sent to SearXNG**
    - SearXNG instance at `https://your-searxng-instance.example.com/search`
    - Parameters: `format=json`, `q=<query>`, `engines=<engine_name>`
    - Request timeout: 20 seconds
3. **Results are processed**
    - Deduplicated and merged by canonical URL across engines/queries.
    - URLs are canonicalized (tracking params stripped, scheme/hostname lowercased, trailing slashes removed).
    - Results should be ranked with simple deterministic heuristics rather than embedding-based reranking.
    - A good default ordering is: number of engines that returned the same canonical URL, then best observed rank, then date if present, then stable title ordering.
    - When results are ready, Aunic should emit a second progress update for the `indicator-area`, for example: `found <N> results...`

## Progress Reporting
`web_search` should report progress while it is running instead of staying silent.

At minimum:
- emit a `query_update` / search-start event when Aunic begins searching for the query
- emit a `search_results_received` / results-received event when results are available

These progress events should be surfaced in the `indicator-area`.
- the existing UI shorthand is:
  - `searching <query>...`
  - `found <N> results...`
- see `notes-and-plans/indicator-area.md`

## Search Depth Settings
- accepts only a single query, and returns the top 10 results
(note: there is dead code around the model choosing quick, balanced or deep depth, this should be removed)

## Search Result Data
Each result carries:
- `source_id` — unique identifier (s1, s2, etc.)
- `title` — page title
- `url` — original URL
- `canonical_url` — normalized URL
- `snippet` — content preview
- `rank` — original search engine rank
- `query_labels` — which queries returned this result
- `date` — published/updated date (if available)

## Formatting Results for the Transcript
Results are rendered as structured text and stored in the `transcript`  markdown table's `content` cell as a JSON array:
```
[{"url": "https://example.com", "title": "Page Title", "snippet": "Content preview..."}, ...]
```
- This format is provider-agnostic — both Anthropic and OpenAI translators pass the content string through as-is (see `notes-and-plans/building-context/building-context.md`).
- Aunic's rendering layer displays these results in the expandable dropdown format described in `notes-and-plans/active-markdown-note/active-markdown-note.md`.

## Rendering in the Transcript
The tool_call and tool_result rows sharing the same `tool_id` are rendered together as a single collapsible entry:
- The collapsed row shows the query text and the number of results.
- Expanding it reveals each result on its own row with page title, snippet, and a link to open the URL.
- (see `notes-and-plans/active-markdown-note/active-markdown-note.md` for the full rendering spec)
