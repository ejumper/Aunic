package runner

import "time"

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
// of Applied, ConflictNotFound, or ConflictAmbiguous is true.
type NoteEditApplyReply struct {
	Applied           bool
	Count             int
	ConflictNotFound  bool
	ConflictAmbiguous bool
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
