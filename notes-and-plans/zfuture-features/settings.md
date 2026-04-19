max `agentic-tool-calls`
- sets the maximum number of `agentic-tool-calls` to keep in the `transcript`
max `chat-transcript` messages
- sets the maximum number of user/assistant messages to keep in context
max `search-queries`
- sets the maximum number of search queries (and its corresponding results) to keep in context.
max `search-results`
- sets the maximum number of results per query to keep in context
@web output
- set whether the `user-search-and-fetch` "@" command outputs to the note or the clipboard
include chats
- set whether to include chat messages in the markdown table in context when in note mode
domain blacklist/whitelist
- configures either a whitelist or blacklist of searchable domains
note-mode `synthesis-pass`
- turn the `synthesis-pass` on or off
transcript placement
- user can decide whether the transcript appears in the same file or is held in .aunic/ 
    - nothing changes as far as the model is concerned, the transcript still is sent to the model in the exact same way. This is purely an aesthetic choice to keep the table from taking space in the markdown file
    - the map feature would need to be updated to recognize "aunic notes" from the transcript present in .aunic/
