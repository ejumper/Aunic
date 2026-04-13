# Aunic (All You Need is Context)
The thesis statement for Aunic is that the chat interface is something LLMs are contorted into doing rather than being the best and most efficient way to interact with them.
- If an LLM is at its heart, something that... 
1. takes in data
2. transforms it according to plain text instructions
- then the interface around it should mirror that as closely as possible. 
    - e.g. have a way to efficiently and transparently build that input data and a way to instruct the LLM how to transform it.
## How to efficiently store data
In every other situation where lots of text data must be efficiently communicated, conversation/chat is not how this is done. It is done through through notes, papers, memos, etc.
- Agentic programs already understand this on some level...
Consider "Plan" mode...
- When complex work needs to be done, the model builds a markdown note, with instructions on what needs to be done.
    - this file can be picked up by any model, with no extra context, and (if well constructed) the model has everything it needs context wise to do the work described in it.
Currently, though, plan mode is a second class citizen to chat.
- Aunic presumes that if that chat interface must be taken out of the picture to carry out complex tasks, it can also be taken out of the picture for simple tasks as well.
    - At which point it calls into question why it isn't the primary interface to begin with.
## How to transparently expose data
If we've established that "plan" mode is a better way to do agentic work, and should therefore be the primary interface over chat, then the next logical step is to expose the markdown note to the user so they can edit it as easily as the model can.
## Building context
So we have now removed the chat interface in favor of a markdown note that can either be edited by the user directly, or edited per the user's instructions by the model.
- So the workflow now becomes creating a markdown note that contains the context required to then do agentic work.
- Now the question becomes how to actually build that context. This is where chat is reintroduced, but now swapped with "plan" mode as a second class citizen.
    - If the note is the most efficient way to deliver information efficiently, it must be acknowledged that chat is the most efficient way to come up with that information.
- So the workflow is now chat about what you want, discussing options, nailing down details, etc. Then building the document from which the actual work will be done.
## Keeping track of what has been done.
When work has been done, it is worth acknowledging that what the chat interface does best is keeping a chronological record of what has happened.
- Therefore, while tool calls done to the markdown note can be considered ephemeral, tool calls done elsewhere, should be recorded as a record of what in the markdown note has been done, and what hasn't.

## Putting it all together.
Aunic's main idea is that the chat transcript is inefficient at storing the context models need to carry out user's instructions, and that a note should be the primary holder of the context. It acknowledges however that chat mode is still needed to discuss with the LLM how to build that context, and that a log of work being done is needed as well. Therefore...
- Aunic's interface is essentially a markdown text editor for holding the `markdown-file` that the user and model can edit, with a `prompt-editor-box` at the bottom for the user to instruct the model what to do from.
- an `indicator-area` exists between the `markdown-file` and the `prompt-editor-box`
- in order to keep all relevant data inside the `markdown-file`, it must be split in two.
    - `note-content` contains the markdown information that makes up the primary context.
    - `transcript` contains a markdown table that holds everything else: `chat-transcript`, `tool-call`/`tool-result` history and `web-searches` 
    - (note: see notes-and-plans/active-markdown-note/active-markdown-note.md and notes-and-plans/building-context/building-context.md for more details)


## Other Benefits
- At the end of a project using a typical agent program, you end up with a very messy database of messages, that holds no additional value. Aunic on the other hand results in a well organized note detailing what the project is and how it was made. 
- It is completely model agnostic and completely vender neutral, you can swap models and lose absolutely nothing.
- Since its just a markdown file, it can be moved, reworked, repurposed for documentation, etc. 
- While the main idea around Aunic is a better way to do agentic work, since Aunic is basically a way to easily build notes with the help of an LLM, it is also of course really good for making notes
- Because the user has full access to the context window, all sorts of cool things can be done to manipulate it using something Aunic calls `edit-commands`
    - you can restrict context, expand it, specify what area is being worked on, etc. (see notes-and-plans/commands/* for more details)


## Additional thoughts
**Aunic emphasizes transparency.**
- The LLM should not be a magical black box that "does stuff" its a tool for helping users work faster, work smarter and work expertly outside their domains of expertise. The LLM should make computers *more* transparent, not less.
**Aunic emphasizes working with the model, not the model working for the user.**
- this means that the user should have access to the same tools the model does
    - the model has `web_search` and `web_fetch`, the user has `@web`
    - the model has `rag`, the user has `@rag`, `@docs`
**The model is a tool that the user controls**
- this means the user must have tools for manipulating the models behavior
**Aunic treats LLMs as a tool not a person**
- much of the infrastructure around LLMs is an attempt to have them feel more "human-like" Aunic rejects this and favors a problem solving approach. 
    - LLMs have limitations, tools can help work around those limitations.

**This is simply an outline of the "idea" behind Aunic. See the rest of notes-and-plans/* for more details**