# Building the `transcript`
(reminder: the API should be built to look "normal" consider how most agentic/chat apps make the API look, and above all else follow that convention, if something in any instructions conflicts with that, notify the user and ask what they'd like to do.)
The `transcript` is the markdown table stored in the active-markdown-note file
- (see `notes-and-plans/active-markdown-note/active-markdown-note.md`)
- It contains the chat history, tool calls, and tool results in a flat, provider-agnostic format.
- To include it in the `model-context`, it must be translated into the appropriate API message format.
## Step 1: Parse the markdown table into row objects
Each row of the transcript table is parsed into a structured object:
```
{"#": 1, "role": "user",      "type": "message",     "tool_name": null, "tool_id": null,     "content": "What's the weather?"}
{"#": 2, "role": "assistant",  "type": "message",     "tool_name": null, "tool_id": null,     "content": "Let me search for that"}
{"#": 3, "role": "assistant",  "type": "tool_call",   "tool_name": "web_search", "tool_id": "call_01", "content": "{\"queries\":[\"weather today\"]}"}
{"#": 4, "role": "tool",       "type": "tool_result", "tool_name": "web_search", "tool_id": "call_01", "content": "[{\"url\":\"https://example.com\",\"title\":\"Weather\",\"snippet\":\"72°F and sunny\"}]"}
{"#": 5, "role": "assistant",  "type": "message",     "tool_name": null, "tool_id": null,     "content": "The weather is 72°F and sunny."}
```
The `content` value is always a JSON value (see `notes-and-plans/active-markdown-note/active-markdown-note.md` for encoding rules). At this step, strings are unquoted (parsed from `"What's the weather?"` to `What's the weather?`), objects and arrays are parsed from their JSON representation.

## Step 2: Group consecutive `assistant` rows
Consecutive rows with `role: "assistant"` are merged into a single group. This handles two cases:
1. **Multiple tool calls in one response**: the model calls two tools at once.
2. **Mixed turns**: the model produces text alongside tool calls (e.g., "Let me search for that" + `web_search`).

```
2 | assistant | message   |            |          | "Let me search for that"
3 | assistant | tool_call | web_search | call_01  | {"queries":["weather today"]}
4 | assistant | tool_call | web_fetch  | call_02  | {"url":"https://example.com"}
```
These three rows become one assistant group. Rows separated by a different role (e.g., a `tool` row between two `assistant` rows) remain separate groups.

**Only `assistant` rows are grouped in this step.** `user` and `tool` rows are left as individual rows — they are handled per-provider in Step 3.

## Step 3: Translate into the target API format
Each group (or individual row) is translated differently depending on the provider.

### Anthropic translation
- **User messages** (`role: user`, `type: message`):
    ```json
    {"role": "user", "content": "What's the weather?"}
    ```
- **Assistant message groups**: all rows in the group become content blocks in a single `content` array.
    - `type: message` rows become `text` blocks
    - `type: tool_call` rows become `tool_use` blocks (content is parsed from JSON string to object for the `input` field)
    ```json
    {"role": "assistant", "content": [
        {"type": "text", "text": "Let me search for that"},
        {"type": "tool_use", "id": "call_01", "name": "web_search",
         "input": {"queries": ["weather today"]}}
    ]}
    ```
    If the group contains only a text message (no tool calls), the shorthand string format is valid:
    ```json
    {"role": "assistant", "content": "The weather is 72°F and sunny."}
    ```
- **Tool results** (`role: tool`): consecutive `tool` rows are merged into a single `role: "user"` message with multiple `tool_result` blocks. This is how Anthropic expects tool results — they go in a user message, not an assistant message.
    ```json
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_01",
         "content": "[{\"url\":...}]"},
        {"type": "tool_result", "tool_use_id": "call_02",
         "content": "Page content..."}
    ]}
    ```
- **Tool errors** (`role: tool`, `type: tool_error`): same structure as tool results with `is_error: true`.
    ```json
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_01",
         "content": "Error: connection timed out", "is_error": true}
    ]}
    ```

### OpenAI compatible translation
- **User messages** (`role: user`, `type: message`):
    ```json
    {"role": "user", "content": "What's the weather?"}
    ```
- **Assistant message groups**: text goes in `content`, tool calls go in a separate `tool_calls` array.
    - `type: message` rows become the `content` string (multiple text rows are joined with newlines)
    - `type: tool_call` rows become entries in the `tool_calls` array (content stays as a JSON string for the `arguments` field)
    ```json
    {"role": "assistant",
     "content": "Let me search for that",
     "tool_calls": [
        {"id": "call_01", "type": "function",
         "function": {"name": "web_search",
                      "arguments": "{\"queries\":[\"weather today\"]}"}}
    ]}
    ```
    If the group contains only tool calls (no text), `content` is `null`:
    ```json
    {"role": "assistant", "content": null,
     "tool_calls": [
        {"id": "call_01", "type": "function",
         "function": {"name": "web_search",
                      "arguments": "{\"queries\":[\"weather today\"]}"}}
    ]}
    ```
- **Tool results** (`role: tool`): each `tool` row becomes its own separate `role: "tool"` message. They are NOT merged like Anthropic.
    ```json
    {"role": "tool", "tool_call_id": "call_01",
     "content": "[{\"url\":...}]"}
    ```
    ```json
    {"role": "tool", "tool_call_id": "call_02",
     "content": "Page content..."}
    ```
- **Tool errors** (`role: tool`, `type: tool_error`): same structure as tool results. OpenAI has no `is_error` flag — the error is communicated through the content text itself.
    ```json
    {"role": "tool", "tool_call_id": "call_01",
     "content": "Error: connection timed out"}
    ```

### Claude Agent SDK
The Agent SDK uses the same content block format as the Anthropic API internally (TextBlock, ToolUseBlock, ToolResultBlock). The Anthropic translation logic applies to the inner content structure — the difference is that messages are wrapped in the SDK's envelope types (UserMessage, AssistantMessage) rather than plain JSON dicts.
*outline the specific envelope wrapping and any SDK-specific fields needed*

## Key differences between the translations

| Aspect              | Anthropic                                          | OpenAI compatible                                |
|---------------------|----------------------------------------------------|--------------------------------------------------|
| Tool call content   | Parsed from string to object in `input`            | Kept as string in `arguments`                    |
| Tool call structure | `content` array of `tool_use` blocks               | `tool_calls` array of `function` objects         |
| Tool results role   | Wrapped in a `role: "user"` message as `tool_result` blocks | Each result is its own `role: "tool"` message |
| Tool result grouping | Consecutive tool results merge into one user message | Each tool result stays as a separate message |
| Tool errors         | `is_error: true` on the `tool_result` block        | No flag — error communicated via content text    |
| Mixed assistant turns | Text and tool_use blocks in a single `content` array | Text in `content`, tool calls in separate `tool_calls` array |
| Null content        | Not applicable — always has content blocks         | `content: null` when assistant has only tool_calls |

## Full walkthrough example
Given this transcript table:
```
| # | role      | type        | tool_name  | tool_id  | content
|---|-----------|-------------|------------|----------|-------------------------------
| 1 | user      | message     |            |          | "Search weather and news"
| 2 | assistant | tool_call   | web_search | call_01  | {"queries":["weather today"]}
| 3 | assistant | tool_call   | web_search | call_02  | {"queries":["top news"]}
| 4 | tool      | tool_result | web_search | call_01  | [{"url":"https://weather.com","title":"Weather","snippet":"72°F"}]
| 5 | tool      | tool_result | web_search | call_02  | [{"url":"https://news.com","title":"News","snippet":"Headlines..."}]
| 6 | assistant | message     |            |          | "Here's what I found..."
```

### Anthropic output
```json
[
    {"role": "user", "content": "Search weather and news"},
    {"role": "assistant", "content": [
        {"type": "tool_use", "id": "call_01", "name": "web_search",
         "input": {"queries": ["weather today"]}},
        {"type": "tool_use", "id": "call_02", "name": "web_search",
         "input": {"queries": ["top news"]}}
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_01",
         "content": "[{\"url\":\"https://weather.com\",\"title\":\"Weather\",\"snippet\":\"72°F\"}]"},
        {"type": "tool_result", "tool_use_id": "call_02",
         "content": "[{\"url\":\"https://news.com\",\"title\":\"News\",\"snippet\":\"Headlines...\"}]"}
    ]},
    {"role": "assistant", "content": "Here's what I found..."}
]
```
Note the alternating role pattern: user → assistant → user (tool results) → assistant. Anthropic requires this alternation.

### OpenAI output
```json
[
    {"role": "user", "content": "Search weather and news"},
    {"role": "assistant", "content": null, "tool_calls": [
        {"id": "call_01", "type": "function",
         "function": {"name": "web_search", "arguments": "{\"queries\":[\"weather today\"]}"}},
        {"id": "call_02", "type": "function",
         "function": {"name": "web_search", "arguments": "{\"queries\":[\"top news\"]}"}}
    ]},
    {"role": "tool", "tool_call_id": "call_01",
     "content": "[{\"url\":\"https://weather.com\",\"title\":\"Weather\",\"snippet\":\"72°F\"}]"},
    {"role": "tool", "tool_call_id": "call_02",
     "content": "[{\"url\":\"https://news.com\",\"title\":\"News\",\"snippet\":\"Headlines...\"}]"},
    {"role": "assistant", "content": "Here's what I found..."}
]
```
Note: each tool result is its own message with `role: "tool"`, and the assistant's `content` is `null` when there's no text.

## Ordering in the model-context
The translated transcript messages are placed first in the `model-context`, followed by a single final user message containing both the `note-snapshot` and the `user-prompt`:
```
[...transcript messages, combined note-snapshot + user-prompt message]
```
The `note-snapshot` and `user-prompt` are combined into one `role: "user"` message rather than sent as two separate user messages. This is required because:
- The Anthropic API rejects consecutive same-role messages (roles must alternate).
- OpenAI-compatible providers running Mistral, Gemma, or Devstral family models enforce strict role alternation via their chat templates and will return a 400 error on consecutive same-role messages.

The combined message places the `note-snapshot` first and the `user-prompt` last, separated by a clear delimiter so the model can distinguish them:
```json
{"role": "user", "content": "<note-snapshot content>\n\n---\n\n<user-prompt content>"}
```
(note: the exact delimiter format can be adjusted — the important thing is that the `user-prompt` is always at the bottom, since models pay the most attention to the end of the message)
