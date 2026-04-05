(note: many of these are for future development, do not implement them unless expressly instructed to)

# /include
held in the metadata for the markdown file is the list of files that should be included with it in the context window.
- /includes are per file. If file A adds file B, file B does not inherently get file A

# /rag --path (or --text) --threshold (0.1-0.9)> --max <number>  --in whitelist/file/path --out blacklist/file/path --to file/to/send/results/to.txt
- lets you append the file with RAG content
	- --path: add matching file paths
	- --text: add the actual text chunks
	- --threshold: matching threshold, (lower = more matches but potentially less relevant, higher = less matches but potentially exclusive)
	- --max: maximum number of matches to include 
	- --in: whitelisted file paths to include
	- --out: blacklisted file paths to exclude
	- --to: file to send the results to, the open file is the default
- this is a pretty major feature, the biggest issue with RAG is its hidden, so the user has no idea if it has pulled relevant information or if it has just added irrelevant information. This feature fixes that, if the user wants to supplement the context with indexed data, they simply run the command, see if its worth keeping, if not delete it, or if they want more lower the theshold
- (note: should have a repetition penalty with a very high threshold, so that if /rag has already been run, it will not add the same chunks twice)

# /prompt-from-note
- pairs with ">> <<" this allows user prompts to be placed in note /prompt-from-note simply triggers the prompt send
- this is useful if you want to annotate what currently exists. 
- --clear: clears all your prompts
- --remove: implies that the model should make changes to the document based on what's in ">> <<" and then remove them from the note after.
- --keep: keeps the content ">> <<" in the note after the prompt (keep is the default)

# /history
- shows the list of prompts you have sent the model.
- lets you select the prompt and see what the file looked like at that point
- from there you can either quit or revert the file to that state
- handled via git? idk.

# /noterize
- this takes any chat transcript located in the file and turns it into a markdown note
    - if there is already, non-chat text in the file, it will incorporate what was discussed in the chat into that text
    - if there is only chat text, it will convert it into a note whole-sale.
        - updates conflicting information
        - incorporates/adds what was decided on
        - incorporates/adds what was noted as not wanted, or not working
- if not chat transcript is detected, it sends an error message
- note: this considers the chat transcript as the most up to date information, if you have decided on A in a non-chat area and decide on B in the chat area, A will be updated to B.

# /isolate
- if present in the user-prompt with a space on either side (or the start/end of the note on one side and a space on the other) when the user prompt is sent, only the currently open note will be attached

# /temp-compact
- will create a separate, compacted (summarized) version of all the files included on the user prompt send and use that instead of the actual files.
    - useful for if you want to ask a general question about what you are working on, but the files all together are too large for a single 