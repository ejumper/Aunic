package tasks

import "time"

// TaskEntry is one parsed checkbox line from a markdown file.
type TaskEntry struct {
	FilePath     string
	LineNumber   int // 1-based
	Text         string
	Checked      bool
	TimerMinutes int        // 0 = none; from {&XhYm}
	DueDate      *time.Time // nil = none
	DueRaw       string     // raw due string e.g. "06/05"
	IndentLevel  int        // 0 = top-level
}

// FileTaskCache is a per-file cache entry persisted to disk.
type FileTaskCache struct {
	Mtime float64     `json:"mtime"`
	Tasks []TaskEntry `json:"tasks"`
}
