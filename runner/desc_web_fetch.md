Fetch a URL and return its content as markdown. Use iteratively after web_search or when you already know the URL.

<parameters>
- url: The URL to fetch. HTTP or HTTPS only. Required.
</parameters>

<limitations>
TRUNCATION: Content is cut at 8000 characters. Long pages return only the beginning — the answer may not appear even if the page is relevant.

BLOCKED PAGES: Some sites block automated requests. Paywalled and 
login-required pages will not return usable content.
</limitations>

<when_truncated>
If the returned content is truncated and didn't contain what you need:
- Fetch a more specific subpage instead of the index or landing page.
- Run a new web_search with more specific terms to find a shorter, more targeted page.
</when_truncated>

<tips>
- If the web_search abstract already answers the question, skip the fetch.
- No prior web_search required — fetch directly when you already know the URL.
- Prefer specific subpages over landing pages (e.g., docs.example.com/api/method rather than docs.example.com).
- The returned title confirms you fetched the intended page before 
  acting on its content.
</tips>
