package main

import (
	"fmt"
	"strings"

	"github.com/atotto/clipboard"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/ejumper/aunic/agent"
	"github.com/ejumper/aunic/editor"
	"github.com/ejumper/aunic/llm"
)

func (m appModel) executeSlashCmd(cmd *agent.SlashCmdResult) (tea.Model, tea.Cmd) {
	switch cmd.Kind {
	case agent.SlashFind, agent.SlashFindReplaceOpen, agent.SlashFindReplace:
		m.promptFocus = false
		m.findMode = true
		m.ag = m.ag.OpenFindCmd(cmd)
		m.editor.SetFocused(false)
		// Trigger initial match highlights if a query was provided.
		if cmd.FindQuery != "" {
			result := m.editor.SetSearch(cmd.FindQuery, false)
			m.ag.Indicator.Set(agent.FormatMatchCount(result.Count, result.Current))
			return m, tea.Batch(m.ag.Indicator.StaleCmd(), m.maybeResizeEditorCmd())
		}
		return m, m.maybeResizeEditorCmd()

	case agent.SlashGotoOpen:
		m.promptFocus = false
		m.gotoMode = true
		m.ag = m.ag.OpenGoto()
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.SlashGoto:
		m.promptFocus = false
		m.ag = m.ag.SetPromptFocus(false)
		m.editor.SetFocused(true)
		if m.editor.GotoLine(cmd.Line) {
			m.ag.Indicator.Set(fmt.Sprintf("Jumped to line %d", cmd.Line))
		} else {
			m.ag.Indicator.SetError("Line doesn't exist")
		}
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashCopy:
		clipboard.WriteAll(cmd.CopyText)
		m.ag.PromptBox.Clear()
		m.ag.Indicator.Set("Copied to clipboard")
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashBg:
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		return m, func() tea.Msg { return tea.Suspend() }

	case agent.SlashModel:
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		m.promptFocus = false
		m.ag = m.ag.SetPromptFocus(false)
		m.editor.SetFocused(true)
		// If a model name was given, try to switch directly.
		if cmd.ModelName != "" {
			for _, e := range llm.AllModels() {
				if strings.EqualFold(e.ModelName, cmd.ModelName) {
					cfg, err := llm.ConfigForModel(e.HarnessKey, e.ModelKey)
					if err == nil {
						m2, switchCmd, switchErr := m.switchToModel(cfg)
						if switchErr != "" {
							m2.ag.Indicator.SetError(switchErr)
							return m2, m2.ag.Indicator.StaleCmd()
						}
						return m2, switchCmd
					}
				}
			}
		}
		// Name didn't match or was empty: open the picker.
		m.modelMode = true
		m.ag = m.ag.OpenModel(buildModelItems())
		return m, m.maybeResizeEditorCmd()

	case agent.SlashFixTables:
		var content string
		if startRow, endRow, hasSel := m.editor.SelectionRows(); hasSel {
			content = editor.NormalizeMarkdownTablesInRange(m.editor.Value(), startRow, endRow)
		} else {
			content = editor.NormalizeMarkdownTables(m.editor.Value())
		}
		m.editor.SetContent(content)
		m.refreshMarkerHighlight()
		m.clearInsertHighlight()
		if err := m.writeNote(); err == nil {
			m.savedValue = content
		}
		m.promptFocus = false
		m.ag = m.ag.SetPromptFocus(false)
		m.editor.SetFocused(true)
		m.ag.Indicator.Set("Tables normalized")
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashNote, agent.SlashChat:
		target := "note"
		if cmd.Kind == agent.SlashChat {
			target = "chat"
		}
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		m.mode = target
		m.ag.SetModeLabel("mode: " + m.mode)
		m.ag.Indicator.Set("Switched to " + m.mode + " mode")
		_ = m.writeNote()
		return m, m.ag.Indicator.StaleCmd()

	case agent.SlashWork, agent.SlashRead, agent.SlashAgentOff:
		switch cmd.Kind {
		case agent.SlashWork:
			m.agentMode = "work"
		case agent.SlashRead:
			m.agentMode = "read"
		default:
			m.agentMode = "off"
		}
		if cmd.CopyText != "" {
			m.ag.PromptBox.SetValue(cmd.CopyText)
		}
		m.ag.SetAgentLabel("agent: " + m.agentMode)
		m.ag.Indicator.Set("Agent mode: " + m.agentMode)
		_ = m.writeNote()
		return m.respawnActiveHarness()

	case agent.SlashWeb:
		if cmd.WebQuery != "" {
			return m.executeWebSearch(cmd.WebQuery, 10)
		}
		return m.openWebQueryBar()

	case agent.SlashTodo:
		if m.agentMode == "off" {
			m.ag.Indicator.SetError(`Error: /todo not available in "agent: off" mode`)
			return m, m.ag.Indicator.StaleCmd()
		}
		m.todoMode = true
		m.promptFocus = true
		m.ag = m.ag.OpenTodoBar(m.todos)
		m.editor.SetFocused(false)
		return m, m.maybeResizeEditorCmd()

	case agent.SlashClear:
		return m.executeClear(cmd.ClearTarget)

	case agent.SlashMarkerInclude:
		return m.executeWrapMarker("!>>", "<<!")
	case agent.SlashMarkerScope:
		return m.executeWrapMarker("@>>", "<<@")
	case agent.SlashMarkerReadOnly:
		return m.executeWrapMarker("$>>", "<<$")
	case agent.SlashMarkerExclude:
		return m.executeWrapMarker("%>>", "<<%")

	}
	return m, nil
}

// executeWrapMarker wraps the editor's active selection with the supplied
// marker tokens. With no active selection it surfaces a red indicator error
// (text must be selected) and makes no edit.
