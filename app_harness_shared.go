package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"

	"github.com/ejumper/aunic/markers"
	"github.com/ejumper/aunic/transcript"
)

// Harness-agnostic helpers shared by the per-harness files
// (app_harness.go, app_harness_claude.go). Anything specific to one
// harness's protocol stays in that harness's file.

// snapshotFingerprint returns a cheap staleness key for a note snapshot:
// (length, fnv64-hash) of the visible text. Any single-byte change perturbs
// the hash, so a matching key means the snapshot is byte-identical to the
// last injection.
func snapshotFingerprint(snap markers.Snapshot) string {
	return fmt.Sprintf("%d:%x", len(snap.Visible), fnv64(snap.Visible))
}

// reloadNoteFromDisk replaces the editor buffer with the note body currently
// on disk. Called after a harness tool call edited the note file so the
// editor reflects the model's changes; the caller's writeNote() restores the
// transcript section if the model's write inadvertently wiped it.
func (m *appModel) reloadNoteFromDisk() {
	raw, err := os.ReadFile(m.filepath)
	if err != nil {
		return
	}
	noteBody, _ := transcript.Split(string(raw))
	m.editor.SetContent(noteBody)
	m.savedValue = noteBody
	m.refreshMarkerHighlight()
	m.clearInsertHighlight()
}

// rowTargetsNoteFile reports whether the tool-call transcript row at rowIdx
// targeted the note file. Relative paths resolve against the note's
// directory, which is also the harness subprocess's cwd.
func (m appModel) rowTargetsNoteFile(rowIdx int) bool {
	row := m.transcriptRows[rowIdx]
	c, err := transcript.DecodeAgentFileCall(row.Content)
	if err != nil {
		return false
	}
	absNote, _ := filepath.Abs(m.filepath)
	absTarget := c.FilePath
	if !filepath.IsAbs(absTarget) {
		absTarget = filepath.Join(filepath.Dir(absNote), absTarget)
	}
	return absNote == absTarget
}

// aunicSessionDir returns (and creates) the directory harness session files
// live in.
func aunicSessionDir() string {
	home, _ := os.UserHomeDir()
	dir := filepath.Join(home, ".local", "share", "aunic", "sessions")
	_ = os.MkdirAll(dir, 0755)
	return dir
}

// snapshotTempPath returns a stable temp file path for the note snapshot,
// keyed by the note's absolute path so multiple open files don't collide.
func snapshotTempPath(absNotePath string) string {
	h := sha256.Sum256([]byte(absNotePath))
	name := "aunic-snap-" + hex.EncodeToString(h[:4]) + ".md"
	return filepath.Join(os.TempDir(), name)
}
