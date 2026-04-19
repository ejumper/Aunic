# Edit Commands
@>> <<@
- On the next prompt, the model is directed to only output inside of these.
- you can wrap them around existing text, or you can place nothing inside them and have the model simply be told where to write
- The rest of the file is still in context, it simply is limited on where it can output text.
!>> <<!
- Include only text inside these in the context window
- this is useful for working feature by feature without compaction, and has the potential to vastly reduce token usage by pointing the model to only what it NEEDS to know for each prompt
%>> <<%
- excludes whats inside from the current context
$>> <<$
- wrap these around text to stop the model from touching them.
- model has full context of the file, but simply won't alter whats inside these
">> <<"
- *note: `/prompt-from-note` has been removed*
- these are treated as normal note context today
- does not override any other editor tools
using multiple edit commands together
- you can use multiple of these in a file, and use them together (with the caveat that some will override each other) as well as layer them inside one another
    - $>> <<$: has primacy, no matter what other editing tools are present, text inside here will not be altered.
        - you could however, wrap them in %>> <<% to exclude them or !>> <<! to show what's inside exclusively. Wrapping them in @>> <<@ would be ignored
    - %>> <<%: this inherently exclusionary, if wrapped in other tools, nothing will happen since the model doesn't know anything is inside them.
(note: see notes-and-plans/building-context/parsing-edit-commands.md for details on how edit commands are actually applied)
(note: "\" before or " " or ' ' around an edit command string with no spaces should make them be treated as normal text, and not be parsed (allows people to write about the edit commands without them being applied))
