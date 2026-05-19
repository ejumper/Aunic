Create or replace the active todo list. Use for multi-step work (3+ steps) so the user can see your plan and you can track progress. Does not end the run.

<parameters>
- todos: Ordered array of short imperative task descriptions (e.g. "Write tests", "Update docs"). Non-empty. Each item must be a non-empty string. Required.
</parameters>

<behavior>
- IDs are assigned automatically starting from 1. The response includes the full items list with IDs and a rendered checkbox view.
- Calling todo_write again replaces the entire list — use it to revise the plan as work progresses (reorder, add, or remove tasks). Keep completed items in the list so the user can see what was done.
- Use todo_done to mark individual items complete as you finish them.
- When every todo is marked done at run end, the list is cleared automatically.
- The user sees the todo list in real-time in the UI. Never print or describe the todo list in your response text.
</behavior>

<when_to_use>
- Multi-step work requiring 3 or more distinct steps
- Tasks where the user would benefit from seeing the plan upfront
- Longer runs where you want to track progress incrementally
- When the user explicitly asks for a todo list or work plan

Call todo_write at the START of the run, before beginning work, so the user sees the plan immediately.
</when_to_use>

<when_not_to_use>
- Single-step or trivial tasks
- Purely informational or conversational requests
- Short research queries that end in a single note_edit call
</when_not_to_use>

<workflow>
1. Call todo_write at the start with all known steps.
2. Call todo_done after completing each step (pass the item's ID from the response).
3. If the plan changes mid-run, call todo_write again with the revised list — existing IDs reset, so update any remaining steps as needed.
</workflow>

<tips>
- Keep descriptions short and imperative: "Add error handling", not "I will add error handling to the auth module".
- Front-load the list with the steps you are most confident about. You can always revise mid-run.
- Call todo_done immediately after each step — do not batch completions at the end.
</tips>
