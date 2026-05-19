Search the web. Returns results with title, url, domain, and abstract.

Use iteratively to find candidate URLs, then call web_fetch to read page content.

<parameters>
- query: The search query string. Required.
- n: Number of results to return (1–25, default 5). Optional.
</parameters>

<query_tips>
- Lead with key terms, not filler words: "React useEffect cleanup leak" not 
  "how do I fix useEffect cleanup in React".
- Quote exact phrases or error messages: "cannot read properties of undefined".
- Include version numbers and technology names when precision matters.
- If the first search misses, rephrase with different terms rather than 
  requesting more results from the same bad query.
</query_tips>

<result_count>
Default 5 is enough for most targeted queries. Use n=10–20 when the topic is broad or when you expect many similar pages and need more candidates to evaluate before fetching.
</result_count>

<tips>
Abstracts are brief snippets — sufficient to decide which URL to fetch, not to answer a question or quote a source directly. Call web_fetch before summarizing or acting on a specific page's content.
</tips>
