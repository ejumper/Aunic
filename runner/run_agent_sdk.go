package runner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/ejumper/aunic/bridge"
	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/modellogs"
	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/transcript"
)

// runAgentSDK drives a Claude Agent SDK session via the Node bridge subprocess.
// Called by Run when cfg.ProviderKind == "agent_sdk". Emits the same
// RunStartedMsg / ToolDispatchedMsg / ToolResultMsg / RunFinishedMsg /
// ChatFinishedMsg / RunErrorMsg / RunCancelledMsg events as the OpenAI path
// so the rest of Aunic (transcript, indicator, modellogs) is oblivious.
func runAgentSDK(ctx context.Context, cfg llm.Config, rc *RunContext, opts RunOptions, emit func(tea.Msg)) {
	mode := opts.Mode
	if mode == "" {
		mode = ModeNote
	}

	sysPrompt := noteSystemPrompt
	if mode == ModeChat {
		sysPrompt = chatSystemPrompt
	} else if opts.WriteScopeCount > 0 {
		sysPrompt = scopedNoteSystemPrompt
	}

	liveTodos := append([]todos.Todo(nil), opts.Todos...)
	userMsg := buildAgentSDKPrompt(rc, opts, liveTodos)

	builtinTools := selectBuiltinTools(opts.AgentMode)
	aunicTools, err := buildAunicToolDefs(mode, opts.AgentMode, opts.WriteScopeCount, opts.NoteWriteForbidden)
	if err != nil {
		emit(RunErrorMsg{Message: err.Error()})
		return
	}

	bridgeDir, err := bridge.ResolveBridgeDir()
	if err != nil {
		emit(RunErrorMsg{Message: err.Error()})
		return
	}

	// Spawn with a cancellable child context so we can outlive ctx briefly
	// for clean shutdown.
	procCtx, procCancel := context.WithCancel(context.Background())
	defer procCancel()
	proc, err := bridge.Spawn(procCtx, bridgeDir)
	if err != nil {
		emit(RunErrorMsg{Message: "bridge: " + err.Error()})
		return
	}
	defer proc.Close()

	effort := cfg.Effort
	if effort == "" {
		effort = "medium"
	}

	start := time.Now()
	slog.Info("run_start_agent_sdk", "mode", mode, "agent_mode", opts.AgentMode,
		"model", cfg.Model, "effort", effort)

	session, err := modellogs.Start(rc.ActivePath)
	if err != nil {
		slog.Warn("modellogs_open_failed", "error", err.Error())
	}
	defer session.Close()
	session.LogRunHeader(cfg.Model+" (agent-sdk)", mode)
	session.LogUserPrompt(opts.UserPrompt)

	if err := proc.SendStart(bridge.StartConfig{
		Model:        cfg.Model,
		Effort:       effort,
		MaxTurns:     maxSteps,
		SystemPrompt: sysPrompt,
		UserPrompt:   userMsg,
		BuiltinTools: builtinTools,
		AunicTools:   aunicTools,
	}); err != nil {
		emit(RunErrorMsg{Message: "bridge: " + err.Error()})
		return
	}

	// Watch for ctx cancellation and forward to the bridge.
	stopWatch := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = proc.SendAbort()
		case <-stopWatch:
		}
	}()
	defer close(stopWatch)

	var totalIn, totalOut int
	var chatText strings.Builder
	endedOnTool := "" // non-empty when an EndsRun tool drove the end
	// pendingBuiltinArgs queues SDK built-in tool call args by (normalized) name
	// so EventToolResultBuiltin can pair the result with the originating args.
	// The bridge emits call→result pairs in order, so a per-name FIFO suffices.
	pendingBuiltinArgs := map[string][]string{}

	for {
		ev, err := proc.NextEvent()
		if err == io.EOF {
			break
		}
		if err != nil {
			slog.Error("bridge_read", "error", err.Error())
			session.LogRunEnd(time.Since(start), "bridge_read_error: "+err.Error())
			emit(RunErrorMsg{Message: "bridge read: " + err.Error()})
			return
		}

		switch ev.Type {
		case bridge.EventStarted:
			session.NextStep()
			emit(RunStartedMsg{})

		case bridge.EventThinking:
			session.LogThinking(ev.Text)

		case bridge.EventText:
			chatText.WriteString(ev.Text)

		case bridge.EventToolCallBuiltin:
			session.LogToolCall(ev.Name, ev.Args)
			norm := normalizeBuiltinName(ev.Name)
			pendingBuiltinArgs[norm] = append(pendingBuiltinArgs[norm], ev.Args)
			emit(ToolDispatchedMsg{Name: norm, ArgsPreview: previewArgs(ev.Args)})

		case bridge.EventToolResultBuiltin:
			session.LogToolResult(ev.Name, ev.Summary, ev.IsError)
			norm := normalizeBuiltinName(ev.Name)
			var callJSON string
			if q := pendingBuiltinArgs[norm]; len(q) > 0 {
				callJSON = q[0]
				pendingBuiltinArgs[norm] = q[1:]
			} else {
				slog.Warn("builtin_result_without_call", "tool", ev.Name)
			}
			emit(ToolResultMsg{
				Name:     norm,
				Summary:  ev.Summary,
				IsError:  ev.IsError,
				CallJSON: callJSON,
			})

		case bridge.EventToolCall:
			// If an EndsRun tool already fired, refuse any further MCP tool
			// calls so the model stops naturally and we reach the result message
			// (which carries accurate total usage).
			if endedOnTool != "" {
				_ = proc.SendToolResult(ev.ID, `{"error":"session ended after note write"}`, true)
				continue
			}

			// Aunic MCP tool — dispatch and send result back to the bridge.
			tool := Lookup(ev.Name)
			if tool == nil {
				slog.Warn("tool_unknown_agent_sdk", "tool", ev.Name)
				errPayload := fmt.Sprintf(`{"error":"unknown_tool","message":"no tool named %q"}`, ev.Name)
				_ = proc.SendToolResult(ev.ID, errPayload, true)
				emit(ToolResultMsg{Name: ev.Name, Summary: "unknown tool", IsError: true})
				continue
			}

			session.LogToolCall(ev.Name, ev.Args)
			emit(ToolDispatchedMsg{Name: ev.Name, ArgsPreview: previewArgs(ev.Args)})

			result := tool.Execute(ctx, rc, ev.Args)

			if err := proc.SendToolResult(ev.ID, result.JSON, result.IsError); err != nil {
				slog.Warn("bridge_send_tool_result", "error", err.Error())
			}

			session.LogToolResult(ev.Name, result.Summary, result.IsError)
			emit(ToolResultMsg{
				Name:       ev.Name,
				Summary:    result.Summary,
				IsError:    result.IsError,
				CallJSON:   ev.Args,
				ResultJSON: result.JSON,
			})

			if result.TodosAfter != nil {
				liveTodos = result.TodosAfter
			}

			// EndsRun tools (note_write, note_edit, note_edit_at) mark the run
			// as complete. Unlike the OpenAI path (which breaks immediately),
			// we let the SDK run to its natural end so the result message
			// provides accurate total token usage. Further MCP calls are
			// refused above; built-in tool calls complete normally.
			if result.EndsRun && !result.IsError {
				endedOnTool = shortName(ev.Name)
			}

		case bridge.EventToolResult:
			// Informational — Aunic already received the result via direct
			// dispatch above. The bridge emits this for symmetry; ignore.

		case bridge.EventUsage:
			totalIn += ev.InputTokens
			totalOut += ev.OutputTokens
			session.AddTokens(ev.InputTokens, ev.OutputTokens)

		case bridge.EventEnd:
			elapsed := time.Since(start)
			// Prefer the authoritative totals from the result message when
			// the bridge provides them; fall back to the accumulated per-turn sum.
			inTok, outTok := totalIn, totalOut
			if ev.InputTokens > 0 {
				inTok, outTok = ev.InputTokens, ev.OutputTokens
			}

			if endedOnTool != "" {
				if todos.AllDone(liveTodos) {
					emit(TodosClearedMsg{})
				}
				slog.Info("run_finish", "reason", endedOnTool, "input_tokens", inTok, "output_tokens", outTok, "elapsed", elapsed)
				session.LogRunEnd(elapsed, endedOnTool)
				emit(RunFinishedMsg{
					EndedOn: endedOnTool,
					InTok:   inTok,
					OutTok:  outTok,
					Elapsed: elapsed,
				})
				return
			}
			switch ev.Reason {
			case bridge.EndCancelled:
				if errors.Is(ctx.Err(), context.Canceled) || errors.Is(ctx.Err(), context.DeadlineExceeded) {
					slog.Info("run_finish", "reason", "cancelled")
					session.LogRunEnd(elapsed, "cancelled")
					emit(RunCancelledMsg{})
					return
				}
				slog.Warn("run_finish", "reason", "cancelled_unexpected", "message", ev.Message)
				session.LogRunEnd(elapsed, "cancelled: "+ev.Message)
				emit(RunErrorMsg{Message: "bridge cancelled: " + ev.Message})
				return
			case bridge.EndError:
				slog.Error("agent_sdk_error", "error", ev.Message)
				session.LogRunEnd(elapsed, "agent_sdk_error: "+ev.Message)
				emit(RunErrorMsg{Message: ev.Message})
				return
			case bridge.EndMaxTurns:
				slog.Warn("run_finish", "reason", "max_turns")
				session.LogRunEnd(elapsed, "max steps reached")
				emit(RunErrorMsg{Message: "max steps reached"})
				return
			case bridge.EndStop:
				if mode == ModeChat {
					if todos.AllDone(liveTodos) {
						emit(TodosClearedMsg{})
					}
					text := chatText.String()
					session.LogChatResponse(text)
					session.LogRunEnd(elapsed, "chat")
					emit(ChatFinishedMsg{
						Text:    text,
						InTok:   inTok,
						OutTok:  outTok,
						Elapsed: elapsed,
					})
					return
				}
				// Note mode ended without a terminating tool call.
				session.LogPlainText(chatText.String())
				session.LogRunEnd(elapsed, "no_tool")
				emit(RunErrorMsg{Message: "model finished without writing to the note"})
				return
			}
		}
	}

	// Reached only if the bridge closed stdout without emitting an end event.
	session.LogRunEnd(time.Since(start), "bridge_eof")
	emit(RunErrorMsg{Message: "bridge exited unexpectedly"})
}

// selectBuiltinTools returns the Claude Code built-in tools to expose for the
// given agent mode. WebFetch and WebSearch are always available (they're the
// Agent SDK equivalents of Aunic's web_search / web_fetch and are intentionally
// allowed even in agent: off to mirror the matrix in claude-agent-sdk.md).
func selectBuiltinTools(agentMode string) []string {
	base := []string{"WebFetch", "WebSearch"}
	switch agentMode {
	case "read":
		return append(base, "Read", "Glob", "Grep", "LS")
	case "work":
		return append(base, "Read", "Glob", "Grep", "LS", "Write", "Edit", "MultiEdit", "Bash", "NotebookEdit")
	default:
		return base
	}
}

// buildAunicToolDefs returns the Aunic-specific tools to expose via the
// in-process MCP server. The set mirrors buildToolParams in runner.go for the
// OpenAI path: scope-active modes swap out note_edit/note_write for
// note_edit_at; noteWriteForbidden drops note_write; chat mode drops both
// note_edit and note_write.
func buildAunicToolDefs(mode, agentMode string, writeScopeCount int, noteWriteForbidden bool) ([]bridge.ToolDef, error) {
	var all []Tool
	switch agentMode {
	case "read":
		all = append(AllTools(), todoWriteTool{}, todoDoneTool{})
	case "work":
		all = append(AllTools(), todoWriteTool{}, todoDoneTool{})
	default:
		all = AllTools()
	}
	scopeActive := mode == ModeNote && writeScopeCount > 0
	out := make([]bridge.ToolDef, 0, len(all)+1)
	for _, t := range all {
		name := t.Name()
		// web_search / web_fetch are handled by the SDK's WebSearch/WebFetch
		// built-ins; don't re-expose them through MCP.
		if name == "web_search" || name == "web_fetch" {
			continue
		}
		if mode == ModeChat && (name == "note_edit" || name == "note_write") {
			continue
		}
		if scopeActive && (name == "note_edit" || name == "note_write") {
			continue
		}
		if noteWriteForbidden && name == "note_write" {
			continue
		}
		schemaBytes, err := json.Marshal(t.Schema())
		if err != nil {
			return nil, fmt.Errorf("marshal schema for %s: %w", name, err)
		}
		out = append(out, bridge.ToolDef{
			Name:        name,
			Description: t.Description(),
			Schema:      schemaBytes,
		})
	}
	if scopeActive {
		schemaBytes, err := json.Marshal(scopedEditSchema(writeScopeCount))
		if err != nil {
			return nil, fmt.Errorf("marshal scoped edit schema: %w", err)
		}
		out = append(out, bridge.ToolDef{
			Name:        "note_edit_at",
			Description: scopedEditDescription(writeScopeCount),
			Schema:      schemaBytes,
		})
	}
	return out, nil
}

// buildAgentSDKPrompt composes the user prompt sent to the SDK. Unlike the
// OpenAI path (which sends snapshot + transcript history + user prompt as a
// chain of message objects), the SDK takes a single string per run. We
// serialize prior transcript rows into the prompt body so the model has the
// same context.
func buildAgentSDKPrompt(rc *RunContext, opts RunOptions, liveTodos []todos.Todo) string {
	var b strings.Builder
	b.WriteString("# Active note (")
	b.WriteString(rc.ActivePath)
	b.WriteString(")\n\n")
	b.WriteString(rc.SnapshotContent)
	b.WriteString("\n\n")

	if len(opts.TranscriptRows) > 0 {
		b.WriteString("# Prior conversation\n\n")
		b.WriteString(renderTranscriptForAgentSDK(opts.TranscriptRows))
		b.WriteString("\n")
	}

	if len(opts.FileAttachments) > 0 {
		b.WriteString("# Attached files\n\n")
		for _, fa := range opts.FileAttachments {
			b.WriteString("## ")
			b.WriteString(fa.Path)
			b.WriteString("\n\n```\n")
			b.WriteString(fa.Content)
			b.WriteString("\n```\n\n")
		}
	}

	b.WriteString("# User prompt\n\n")
	b.WriteString(userTextWithTodos(opts.UserPrompt, liveTodos))
	return b.String()
}

// renderTranscriptForAgentSDK renders prior transcript rows as text so the
// model has the same context the OpenAI path gets via real prior messages.
// This is intentionally lossy — tool calls and results are summarized.
func renderTranscriptForAgentSDK(rows []transcript.Row) string {
	var b strings.Builder
	for _, r := range rows {
		switch r.Type {
		case transcript.TypeMessage:
			var msg transcript.MessageContent
			_ = json.Unmarshal(r.Content, &msg)
			b.WriteString("## ")
			b.WriteString(string(r.Role))
			b.WriteString("\n\n")
			b.WriteString(msg.Text)
			b.WriteString("\n\n")
		case transcript.TypeToolCall:
			b.WriteString("## tool_call: ")
			b.WriteString(r.Tool)
			b.WriteString("\n\n")
			b.Write(r.Content)
			b.WriteString("\n\n")
		case transcript.TypeToolResult:
			b.WriteString("## tool_result: ")
			b.WriteString(r.Tool)
			b.WriteString("\n\n")
			b.Write(r.Content)
			b.WriteString("\n\n")
		}
	}
	return b.String()
}

// normalizeBuiltinName maps SDK built-in tool names to the names used by
// recordRunnerToolInTranscript. Most built-ins already match (Read, Write,
// Edit, Bash, Grep, Glob); only WebSearch/WebFetch need rewriting so they
// land on Aunic's existing web_search / web_fetch transcript encoders.
func normalizeBuiltinName(name string) string {
	switch name {
	case "WebSearch":
		return "web_search"
	case "WebFetch":
		return "web_fetch"
	default:
		return name
	}
}
