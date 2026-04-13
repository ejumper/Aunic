/clear-history
- clears all entries from `transcript`
/note, /chat, /read, /work, /off
- changes modes
/context
- prints context
/model
- opens model switcher
/include <file-path>
- allows for splitting the `note-content` sent to the model across multiple files.
    - the transcript stays file specific, all that's being combined is the `note-content` (the portion above the transcript)
    - this is true for all included files. on model run, you essentially taking the current process for sending the file to the model in the `note-snapshot` and performing it on each included file, then appending those files together (make sure each file is still labeled with the file name though and it is clear to the model when it note_edit/writes which edits go where with a header comment or whatever makes it most clear to the model)
        - they are included in the model-context the same way a single file is, as the user-message prior to the user-prompt. Don't over complicate it, its just a conjoined version of the normal process
- UI already has display method for viewing included files, by clicking on the file name.
    - clicking on a file from the list should save and close the current note and open the selected one.
    - no note content, just a list of the file names. Have a fallback where if 2 files with the same name are included the parent directories are shown, going up until the paths are differentiable (e.g. show as little of the path as is required to differentiate them)
    - the file list should have 2 buttons to the left of the file name: [X] to remove the file and [*] to toggle the file "active"/"inactive"
        - inactive file remain in the list, but are not included in runs until the user "reactivates" them.
        - these changes persist until changed by the user
- "/include ./example/path/to/file.md" would include just that file
- "/include ./example/path/to/directory" would include the entire directory non-recursively, including any files added in the future.
- "/include -r ./example/path/to/directory" would include the entire directory recursively, including any files added in the future.
- "/exclude <file-or-directory-path>" removes those files and directories
- "/isolate <prompt>" only sends the current file on the next run (a way to temporarily exclude files)
- "/isolate /path/one /path/two <prompt>" will only include the files listed.
    - /isolate is specific to the run only, then it returns to normal
- include is file specific, no syncing. so If file A includes file B that does not mean file B includes file A
    - the file owns its own include list and has no awareness of other files include lists, so no conflicts or loops can occur
- data stored in per-note metadata in tui_prefs.json
- the only limit is the context window size. So for the purposes of include as many files as the user wants can be included, the model API/sdk will stop the run if the size is too big
- edit commands are parsed for every note in the include list, 