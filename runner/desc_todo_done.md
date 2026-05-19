Mark a todo as completed. Does not end the run — call it as each task finishes.

<parameters>
- id: The todo's ID number (the N in #N as shown in the active todos list). Required.
</parameters>

<behavior>
- Returns the full updated todo list and a remaining list of incomplete todos.
- Todos persist between runs — the done state is saved to the note.
- If id does not match any active todo, returns todo_not_found.
</behavior>

<tips>
- Call immediately after completing each task — do not batch completions.
- Check the remaining list in the response to know what to work on next.
- Use todo_write at the start of a multi-step run to plan tasks, then call todo_done as each is finished.
</tips>
