# Parsing edit commands
(note: see notes-and-plans/commands/edit-commands.md for explanations on what the various edit commands do)
"edit commands" are parsed in the following order. 
(note: the edit command wrapper strings themselves are removed from the `note-snapshot` as they're applied. The model should not see them, they will only confuse it.)
1. %>> <<%
    - Any text wrapped in these symbols is removed from the `note-snapshot`
2. !>> <<!
    - All text NOT wrapped in these symbols is removed from context
3. $>> <<$
    - Anything inside these symbols is labeled `READ_ONLY - EDITS_REJECTED`
        - labels are made in HTML comments like... 
        \n<!--START READ ONLY SECTION - EDITS ARE AUTO-REJECTED-->
        example text
        <!--/END READ ONLY SECTION-->\n
        - auto-rejection will also occur, if edits are attempted, a system prompt is added wherever it makes the most sense, saying something along the lines of "you tried to edit a read only area, this is expressly forbidden and is autorejected". to discourage loops where the model tries over and over again to edit read only areas.
        - (note: this marking has priority over "@>> <<@", meaning if the user places "$>> @>> <<@ <<$", "@>> <<@" will have no effect)
4. @>> <<@
    - The inverse of $>> <<$ Everything outside these symbols is labeled `READ_ONLY - EDITS_REJECTED`
        - same formatting as $>> <<$
(note: "\" before or " " or ' ' around an edit command string with no spaces should make them be treated as normal text, and not be parsed (allows people to write about the edit commands without them being applied))