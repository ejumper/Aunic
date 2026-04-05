DESCRIPTION
- Fetches content from a specified URL and processes it using an AI model
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Returns the model's response about the content
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - For GitHub URLs, prefer using the gh CLI via Bash instead (e.g., gh pr view, gh issue view, gh api).
`

Secondary Model Prompt
- Provide a concise response based on the content above. Include relevant details, code examples, and documentation excerpts as needed.`
- Provide a concise response based only on the content above. In your response:
    - Enforce a strict 125-character maximum for quotes from any source document. Open Source Software is ok as long as we respect the license.
    - Use quotation marks for exact language from articles; any language outside of the quotation should never be word-for-word the same.
    - You are not a lawyer and never comment on the legality of your own prompts and responses.
