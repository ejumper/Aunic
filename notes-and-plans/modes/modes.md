# What is a mode
- Conceptually, modes are the ways and areas the user communicates with the model and how the model can do agentic work. 
- Literally, they define the tools available to the LLM and where they can use them.
- There 2 sets of modes, work/read/off and note/chat
    - work/read/off: defines what the model can do outside the `active-markdown-note`
        - work lets it read and make changes outside the note
        - read only lets it find and read information outside the `active-markdown-note`
        - off doesn't let it do anything outside the `active-markdown-note`
        - (note: granular permissions will still be configurable in settings for work and read modes, this just lets the user quickly swap out what the model can "see" and "do" without having to reconfigure the permissions.)
    - chat/note: simply defines the output method as specified below
(note: each tool listed for each mode has a corresponding directory in notes-and-plans/tools/ with markdown file(s) explaining how they should work)

# note-mode and chat-mode
`note-mode` and `chat-mode` are a toggle, either one or the other are on.

## Note Mode
`note-mode` is Aunic's main feature, it is what differentiates it from something like Claude Code, Codex, Aider, etc. The idea is something similar to Claude Code's plan mode, where a file is created which provides the primary context for the model to do agentic work from, rather than relying on a chat transcript to provide the context. `note-mode` is a way for the user and the model to build that "plan" together. The user either writes out text for the plan itself, or it sends `user-prompts` to the model telling it what to write, edit, etc.
- `note-mode` is specifically for outputting to the `note-content`, assistant messages should never appear in the transcript in note-mode.
- The only note-mode specific transcript entry that should ever take place is for search and fetch. So, assuming that "Work:" is set to off, only web_search and web_fetch related items should be added in transcript.
- the synthetic pass that "turns this into note-content", is specific to `work-mode`, it is meant to verify that the edit to the note reflects everything the model just did.
- `note-mode` the flow is: "user asks it to make an alteration to the `note-content` based on a user prompt". So if the user says "What are the rules of baseball" the implicit direction is to write down the rules of baseball in the note, not reply to the question in the transcript.
### Tools Available
- `note-edit`
    - this is how the model edits the document.
        - it is functionally the same `edit` tool exposed to `work-mode`, it is simply sandboxed to the `note-content` while `work-mode`'s `edit` tool is inverted, it can edit anywhere but the `note-content` (barring permission settings of course)
- `note-write`
    - this is how the model replaces the current working copy of the document.
        - it performs whole-note replacement of `note-content`
- `web-search`
    - allows the model to generate web-searches
- `web-fetch`
    - allows the model to fetch web pages from URLs
### Work Area
`note-mode` is for editing `note-content` only. 
- (note: in later versions there will be a way to split the `note-content` across multiple files at which point, `note-mode` will be allowed to edit any of those included files)
- `web-searches`, `web-fetches` go to `transcript` all other tool calls are ephemeral and disappear when the run ends, reverting is handled through undo/redo history and note versions (later feature)
### System Prompts
*since the model has no concept what note-mode is, it will likely be necessary to inject some instruction on how it should behave*
### Note-Mode Synthesis Pass
When Aunic completes a run in `note-mode`, and `work-mode` or `read-mode` was also on during that run, and the model successfully executed tool-calls using `work-mode` or `read-mode` specific tools, when the model outputs an assistant message with no tool call indicating it is done, Aunic forces a final pass asking the model to synthesize that work back into the note.
- It should be presented with the `note-snapshot` plus the `latest-run-log` (the portion of `run-log` from the last user message up to the `assistant-message` it just output).
- It is instructed to look at the the `latest-run-log` and
    - add new information from it to the spot in `note-content` it best fits at
    - update information in `note-content` that has changed
    - remove information from `note-content` that has been made irrelevant
- to complete this task, it should be given access to the `note-edit` and `note-write` tools
    - It should complete this in a single pass (if testing proves this insufficient more passes can be allowed)
- (note: in later versions, the `synthesis-pass` will be able to be turned off in the settings, in which case the final "assistant responses is just appended to the bottom of `file-content`)


## Chat Mode
`chat-mode` essentially switches to the typical agent program behavior, the only difference is it stores messages in a markdown table instead of a database 
- (note: see notes-and-plans/building-context/building-context.md and notes-and-plans/active-markdown-note/active-markdown-note.md for more details)
### Tools Available
- `web-search`
    - allows the model to generate web-searches
    - recorded in the `transcript` the same way as `note-mode` does
- `web-fetch`
    - allows the model to fetch web pages from URLs
    - recorded in the `transcript` the same way as `note-mode` does
### Work Area
everything done in `chat-mode` is recorded in the `transcript`
## System Prompts
*since chat is the model's default behavior additional prompting might be unnecessary*

# work-mode vs read-mode vs off
## Work Mode
`work-mode` expands the toolset to include tools for making changes outside the `note-content` allowing for agentic work such as writing code in repos, and everything else all the other agent programs can do.
- in `note-mode`, if outside-note work happens and the run ends naturally with an assistant message and no tool call, Aunic may run the synthesis pass described above so the note-content is updated to reflect that work.
## Tools Available
- `bash`
    - runs shell commands in the project environment.
    - (note: see notes-and-plans/tools/bash-tool/* for more details)
- `edit`
    - this is how the model writes to, edits, or otherwise manipulates files.
        - it is functionally the same `edit` tool exposed to `note-mode`, except its inverted, it can edit anywhere but the `note-content` (barring permission settings of course)
    - (note: see notes-and-plans/tools/edit-tool/* for more details)
- `write`
    - creates new files or overwrites existing ones.
    - (note: see notes-and-plans/tools/write-tool/* for more details)
- `read`
    - reads file contents, including specific line ranges.
    - (note: see notes-and-plans/tools/read-tool/* for more details)
- `grep`
    - searches file contents with regular expressions.
    - (note: see notes-and-plans/tools/grep-tool/* for more details)
- `glob`
    - finds files using glob patterns like **/*.js or src/**/*.ts.
    - (note: see notes-and-plans/tools/glob-tool/* for more details)
- `list`
    - lists files and directories in a path, with optional glob filtering.
    - (note: see notes-and-plans/tools/list-tool/* for more details)
## System Prompts
*since having this on implies the user wants things to happen to other files, a system prompt nudging the model towards doing work elsewhere might be useful*

# Read Mode
`read-mode` expands the toolset to include tools for gathering information from outside `note-content`. The tools added are only meant for finding information, not altering files.
- `read`
    - reads file contents, including specific line ranges.
    - (note: see notes-and-plans/tools/read-tool/* for more details)
- `grep`
    - searches file contents with regular expressions.
    - (note: see notes-and-plans/tools/grep-tool/* for more details)
- `glob`
    - finds files using glob patterns like **/*.js or src/**/*.ts.
    - (note: see notes-and-plans/tools/glob-tool/* for more details)
- `list`
    - lists files and directories in a path, with optional glob filtering.
    - (note: see notes-and-plans/tools/list-tool/* for more details)
## System Prompts
*since having this on implies the user wants the model to explorer files outside the current-markdown-note, a system prompt nudging the model looking for and reading files elsewhere might be useful*
