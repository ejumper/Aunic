package main

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/atotto/clipboard"
	"github.com/ejumper/aunic/editor"
	"github.com/ejumper/aunic/runner"
)

// app_conflict.go centralizes the conflict-resolution flow that fires when a
// runner-side edit/write would clobber concurrent user edits.
//
// The three movements:
//
//	enter*Conflict     — runner detects the conflict, opens the conflict bar
//	                     and parks the pending apply on appModel.
//	resolveConflict*   — user picks a side; we either reject the apply (user
//	                     wins, edit goes to clipboard) or revert the editor to
//	                     the run-start snapshot and apply (model wins).
//	cancelPendingConflict — run aborted / cancelled before the user picked;
//	                     drop pending pointers and close the bar.
//
// Invariant: at most one of m.pendingNoteEdit / m.pendingNoteWrite is non-nil
// at any time. The enter* helpers set the appropriate pointer; the resolve*
// helpers branch on which is set. Each helper clears both pointers on exit so
// the invariant holds inductively.

// enterEditConflict parks the pending note_edit apply and opens the conflict
// bar. label is the error-indicator text; the two callers differ only in
// whether they include a match count for the ambiguous case.
func (m appModel) enterEditConflict(msg *runner.NoteEditApplyMsg, label string) (appModel, tea.Cmd) {
	m.pendingNoteEdit = msg
	m.conflictMode = true
	m.ag = m.ag.OpenConflict()
	m.promptFocus = false
	m.editor.SetFocused(false)
	m.ag.Indicator.SetError(label)
	return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
}

// editConflictLabel builds the error-indicator label for a note_edit conflict.
// Ambiguous conflicts include the match count; other conflicts use a flat
// "Conflict on note edit!" message.
func editConflictLabel(ambiguous bool, count int) string {
	if ambiguous {
		return fmt.Sprintf("Conflict on note edit! (%d matches)", count)
	}
	return "Conflict on note edit!"
}

// enterWriteConflict parks the pending note_write apply and opens the
// conflict bar. Triggered when the buffer hash has changed since the run
// started.
func (m appModel) enterWriteConflict(msg *runner.NoteWriteApplyMsg) (appModel, tea.Cmd) {
	m.pendingNoteWrite = msg
	m.conflictMode = true
	m.ag = m.ag.OpenConflict()
	m.promptFocus = false
	m.editor.SetFocused(false)
	m.ag.Indicator.SetError("Conflict on note write!")
	return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
}

// resolveConflictUserWins is the body of the ConflictUserWinsMsg handler.
// The runner's apply is rejected; the proposed edit is copied to the
// clipboard so the user can decide what to do with it. The runner goroutine
// continues (its Reply channel gets the rejection).
func (m appModel) resolveConflictUserWins() (appModel, tea.Cmd) {
	m.conflictMode = false
	m.ag = m.ag.CloseConflict()
	m.editor.SetFocused(true)
	m.conflictJustResolved = true
	if m.pendingNoteEdit != nil {
		clipboard.WriteAll(m.pendingNoteEdit.New)
		m.pendingNoteEdit.Reply <- runner.NoteEditApplyReply{ConflictNotFound: true}
		m.pendingNoteEdit = nil
	} else if m.pendingNoteWrite != nil {
		clipboard.WriteAll(m.pendingNoteWrite.Content)
		m.pendingNoteWrite.Reply <- runner.NoteWriteApplyReply{HashMismatch: true}
		m.pendingNoteWrite = nil
	}
	m.ag.Indicator.Set("Edit copied to clipboard and not applied")
	return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.runStream.NextCmd(), m.maybeResizeEditorCmd())
}

// resolveConflictModelWins is the body of the ConflictModelWinsMsg handler.
// The user-side concurrent edits are discarded: the editor is reverted to the
// run-start snapshot before the runner's apply lands, guaranteeing the
// preconditions (e.g. old_string presence) the model assumed.
func (m appModel) resolveConflictModelWins() (appModel, tea.Cmd) {
	m.conflictMode = false
	m.ag = m.ag.CloseConflict()
	m.editor.SetFocused(true)
	if m.pendingNoteEdit != nil {
		// Revert to snapshot so old_string is guaranteed to be present.
		prev := m.runSnapshotContent
		m.editor.SetContent(m.runSnapshotContent)
		res := m.editor.ApplyNoteEdit(m.pendingNoteEdit.Old, m.pendingNoteEdit.New, m.pendingNoteEdit.ReplaceAll)
		content := editor.NormalizeMarkdownTables(m.editor.Value())
		m.editor.SetContent(content)
		m.refreshMarkerHighlight()
		m.setInsertHighlight(prev, content)
		if err := m.writeNote(); err == nil {
			m.savedValue = content
		}
		m.pendingNoteEdit.Reply <- runner.NoteEditApplyReply{Applied: res.Applied, Count: res.Count}
		m.pendingNoteEdit = nil
	} else if m.pendingNoteWrite != nil {
		prev := m.editor.Value()
		normalized := editor.NormalizeMarkdownTables(m.pendingNoteWrite.Content)
		m.editor.SetContent(normalized)
		m.refreshMarkerHighlight()
		m.setInsertHighlight(prev, normalized)
		if err := m.writeNote(); err == nil {
			m.savedValue = normalized
		}
		m.pendingNoteWrite.Reply <- runner.NoteWriteApplyReply{Applied: true}
		m.pendingNoteWrite = nil
	}
	return m, tea.Batch(m.runStream.NextCmd(), m.maybeResizeEditorCmd())
}

// cancelPendingConflict drops the conflict UI and any pending apply when the
// run aborts before the user picks a side. Called from RunErrorMsg /
// RunCancelledMsg handlers; safe to call when no conflict is active.
func (m appModel) cancelPendingConflict() appModel {
	if !m.conflictMode {
		return m
	}
	m.conflictMode = false
	m.ag = m.ag.CloseConflict()
	m.pendingNoteEdit = nil
	m.pendingNoteWrite = nil
	m.editor.SetFocused(true)
	return m
}
