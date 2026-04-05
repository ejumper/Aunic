# General
- The TUI will be built using prompt_toolkit
- The colors will plug in to the terminal theme configured colors
- No background will be set, the terminal background will be used.

# Sections
## top_bar
- contains the open file name, centered vertically.
    - no background, title uses the blue color, text is bolded and underlined
    - just tall enough to to fit the file name with reasonable padding.
- clicking the title shows a dropdown menu with all the included files for quickly jumping between them
    - if the file is unsaved, a warning appears before switching asking the user to save, don't save, or cancel
## text-editor
The area for the user to view/edit the file.
in prompt_toolkit, it should use TextArea
- for markdown files I'd like good syntax highlighting and minor rendering when practical
    - headers should be bolded and italicized to differentiate them
    - *italics* should be italicized
    - **bold** text should use the bold font variant
    - ***bold-italics*** should use the respective variant
    - `code-wrappers` and code blocks (```) should use the blue color
    - line containing only *** and --- should render as a line across the editor, but switch to the symbol with the cursor is on the line
    - soft wraps on lines that are indented and start with "- " or "* " or "+ " or "<number>. " or "<number>) " should match the indentation level of the start of the line
- some custom syntax highlighting is needed for the editor commands
    - @>> <<@: should use the cyan color
    - !>> <<!: should use the green color
    - %>> <<%: should use the red color
    - $>> <<$: should use the magenta color
    - ">> <<": should have a blue background with white text
- for Aunic markdown files to remain "neat", there needs to be obsidian style folding/unfolding for headers, indented content and ordered/unorderend lists. 
## transcript-view
- the transcript should render separately from the raw markdown editor
- chat messages should be human-readable rather than exposing raw transcript-table rows
- tool rows should support collapse/expand behavior, filtering, sorting, and row deletion
## indicator-area
this is where the model/program can convey important text information to the user.
- error messages
- file saved info
- current step the model is working on
- latest tool call from the model (using tool call `purpose`)
- in note_mode, this is where natural-stop completion and synthesis-pass status go.
text is italicized and (if possible, its not that important) the thin/light variant
width = window width and by default it is only as tall as it needs to be to fit a single line of text with reasonable padding, although it can add up to 2 extra lines worth of height from the text_editor's height if needed to fit text. 
## prompt-editor-box
This is where the user prompt is entered and the main controls are. The Claude Code chat box is a good reference point. 
![alt text](prompt_editor_box_example.png)
Notice how it has a section to enter text, then a row at the bottom for the controls.
### prompt-editor
- The text section should also use TextArea in prompt_toolkit with accept_handler for sending user-prompts
- Enter should add new lines, ctrl+r should send the prompt
- both softwraps and new lines from enter should cause the text area to grow upwards by 1 line, moving `indicator_area` upwards too and shrinking the height of the text_editor
    - up to a max of 10 lines, then it overflows with scroll/cursor position to navigate.
### control-row
from right to left...
- send user_prompt (square with an up arrow icon)
- `note_mode`/`chat_mode` toggle
- `work_mode` `off`/`read`/`work` toggle
- `model_picker` dropdown
(note: more buttons will be added here as needed, but this seems like a good start.)
