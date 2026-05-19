package runner

import (
	"time"

	"github.com/ejumper/aunic/todos"
)

// RunStartedMsg is emitted once at the start of a run.
type RunStartedMsg struct{}

// ToolDispatchedMsg is emitted just before a tool call executes.
type ToolDispatchedMsg struct {
	Name         string
	ArgsPreview  string
}

// ToolResultMsg is emitted after a tool call completes (success or error).
// CallJSON and ResultJSON carry the raw arguments string and the tool's result
// payload so the app can persist a transcript row for tools that warrant one
// (web_search, web_fetch). They are not used for indicator rendering.
type ToolResultMsg struct {
	Name       string
	Summary    string
	IsError    bool
	CallJSON   string
	ResultJSON string
}

// RunFinishedMsg is emitted when the run ends successfully on a note_edit /
// note_write tool call.
type RunFinishedMsg struct {
	EndedOn string // "edit" or "write"
	InTok   int
	OutTok  int
	Elapsed time.Duration
}

// ChatFinishedMsg is emitted when a chat-mode run ends on a plain-text
// assistant reply (no tool call). app.go appends the user prompt and the
// assistant Text as transcript message rows.
type ChatFinishedMsg struct {
	Text    string
	InTok   int
	OutTok  int
	Elapsed time.Duration
}

// RunErrorMsg is emitted on a fatal run error (API error, max steps, etc.).
type RunErrorMsg struct {
	Message string
}

// RunCancelledMsg is emitted when the run's context is cancelled.
type RunCancelledMsg struct{}

// RunStreamDoneMsg is emitted by the Stream drainer after the runner's event
// channel closes. It carries no semantic meaning beyond "stop pumping".
type RunStreamDoneMsg struct{}

// VisionUnsupportedMsg is emitted when the first API call fails with an error
// indicating the model does not support image content. The runner retries
// transparently without the images; app.go shows a brief indicator.
type VisionUnsupportedMsg struct{}

// NoteEditApplyMsg is sent by the note_edit tool when it wants the main loop
// to apply a find/replace to the editor buffer. The tool blocks on Reply
// until the main loop processes the message.
type NoteEditApplyMsg struct {
	Old        string
	New        string
	ReplaceAll bool
	Reply      chan NoteEditApplyReply
}

// NoteEditApplyReply reports the outcome of a NoteEditApplyMsg. Exactly one
// of Applied, ConflictNotFound, ConflictAmbiguous, or ConflictProtected is
// true. ConflictProtected indicates the requested edit overlaps a $>><<$
// range; the model should retry with a different old_string.
type NoteEditApplyReply struct {
	Applied           bool
	Count             int
	ConflictNotFound  bool
	ConflictAmbiguous bool
	ConflictProtected bool
}

// NoteWriteApplyMsg is sent by the note_write tool when it wants the main
// loop to replace the entire editor buffer.
type NoteWriteApplyMsg struct {
	Content string
	Reply   chan NoteWriteApplyReply
}

// NoteWriteApplyReply reports the outcome of a NoteWriteApplyMsg.
type NoteWriteApplyReply struct {
	Applied      bool
	HashMismatch bool
}

// TodoWriteApplyMsg is sent by the todo_write tool when it wants the main
// loop to replace the persistent todo list with Texts. The tool blocks on
// Reply until the main loop assigns IDs and persists the list.
type TodoWriteApplyMsg struct {
	Texts []string
	Reply chan TodoWriteApplyReply
}

// TodoWriteApplyReply reports the outcome of a TodoWriteApplyMsg. Items is
// the post-write state with auto-assigned IDs.
type TodoWriteApplyReply struct {
	Applied bool
	Items   []todos.Todo
}

// TodoDoneApplyMsg is sent by the todo_done tool when it wants the main loop
// to mark the todo with the given ID as done.
type TodoDoneApplyMsg struct {
	ID    int
	Reply chan TodoDoneApplyReply
}

// TodoDoneApplyReply reports the outcome of a TodoDoneApplyMsg. NotFound is
// true when no todo with the requested ID exists. Items is the post-update
// state.
type TodoDoneApplyReply struct {
	Applied  bool
	NotFound bool
	Items    []todos.Todo
}

// TodosClearedMsg is emitted by the runner at run end when every active todo
// is checked. The main loop clears the persistent todo list in response.
type TodosClearedMsg struct{}

// NoteEditAtApplyMsg is sent by the note_edit_at tool when it wants the main
// loop to splice scoped edits into the live editor buffer. The tool blocks
// on Reply until the main loop processes the edits.
type NoteEditAtApplyMsg struct {
	Edits map[string]string
	Reply chan NoteEditAtApplyReply
}

// NoteEditAtApplyReply reports the outcome of a NoteEditAtApplyMsg.
// AppliedSlots holds the slot numbers (ascending) that were actually
// modified; ValidationError carries a user-readable message when a slot
// key is malformed or doesn't exist in the current note.
type NoteEditAtApplyReply struct {
	Applied         bool
	AppliedSlots    []int
	HashMismatch    bool
	ValidationError string
}
