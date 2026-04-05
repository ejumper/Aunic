# Conflict Resolution
If a changed section conflicts with a live-file edit, the user is presented with options. This is the same resolution flow as the existing design:
1. **User wins**
    - The model's edit for that section is rejected.
    - In the `note-snapshot` the text matching the search term that resulted in the conflict is wrapped in...
        \n<!--START READ ONLY SECTION - EDITS ARE AUTO-REJECTED-->
        altered text
        <!--/END READ ONLY SECTION-->\n
    - The model is prompted for its next tool call.
    - (note: the HTML comment expands on subsequent user wins. example: if there are 10 lines of text, and the user has altered lines 3-6, the model tries to edit lines 3-4 and the user selects "user wins", html is wrapped around 3-4), if the model tries to edit line 5-6 on the next turn, the "END READ ONLY SECTION" is then moved to after line 6
2. **Model wins**
    - The model's version of that section overwrites the live file.
## modes to add later
3. **Auto-resolve**
    - The model is given a special pass (does not count against any caps).
    - The model is shown both the live version and its own version of the conflicting section.
    - The model may:
        1. `blend` — rewrite the section to retain the substance of both versions.
        2. `rewrite-and-append` — place its change above or below the user's edit, rewritten to fit.
4. **User-resolve**
    - The prompt input box is replaced with a `cancel-last-prompt` button and a `show-conflict-options-again` button.
    - The text editor splits vertically showing the live version and the model's version side by side.
    - The user edits either version, then selects `apply-changes` on whichever they want to keep.
## UI
*should start out as a simple 2 button option that replaces the `prompt-input-box`, then the other 2 buttons should be added, then a diff screen should replace the `text-editor` so the user can see what the options actually are, but write this up in more detail*