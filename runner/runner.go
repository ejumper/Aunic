package runner

import (
	"context"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	openai "github.com/charmbracelet/openai-go"
	"github.com/charmbracelet/openai-go/packages/param"
	"github.com/charmbracelet/openai-go/shared"

	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/modellogs"
	"github.com/ejumper/aunic/todos"
	"github.com/ejumper/aunic/transcript"
)

const maxSteps = 100

// ModeNote and ModeChat select runner behavior on plain-text responses and
// available tool names. ModeNote (the original behavior) nudges the model
// back to calling note_edit/note_write; ModeChat accepts plain text as the
// run's terminal reply and excludes the note-mutating tools from the API
// tool list.
const (
	ModeNote = "note"
	ModeChat = "chat"
)

//go:embed system_note.md
var noteSystemPrompt string

//go:embed system_scoped.md
var scopedNoteSystemPrompt string

//go:embed system_chat.md
var chatSystemPrompt string

// FileAttachment is a user-provided text file passed as a pseudo-Read call.
type FileAttachment struct {
	Path    string
	Content string
}

// RunOptions carries everything Run needs beyond the shared LLM config and the
// RunContext.
type RunOptions struct {
	Mode            string // ModeNote or ModeChat; empty defaults to ModeNote
	AgentMode       string // "off", "read", or "work"; empty defaults to "off"
	UserPrompt      string
	TranscriptRows  []transcript.Row
	FileAttachments []FileAttachment
	PendingImages   [][]byte // raw image bytes (PNG or JPEG) from clipboard
	Todos           []todos.Todo
	// WriteScopeCount is the number of active @>> <<@ slots in the snapshot.
	// When > 0, note_edit / note_write are dropped from the API tool list and
	// note_edit_at is registered in their place.
	WriteScopeCount int
	// NoteWriteForbidden drops note_write from the API tool list — set by
	// content-shaping markers that make a single full-note write unsafe
	// (multiple !>><<! spans, or a %>><<% in the middle of writable
	// content). Has no effect when WriteScopeCount > 0 (scope swap already
	// removes note_write).
	NoteWriteForbidden bool
}

func plainTextNudge(response string) string {
	return fmt.Sprintf(
		"The model returned a plain text response instead of calling note_edit or note_write.\nPlain text response: %q\nPlease integrate this response into the active note using note_edit or note_write.",
		response,
	)
}

// Stream is the channel of tea.Msg events emitted by a run. Pump it via
// NextCmd() until you receive RunStreamDoneMsg.
type Stream struct {
	ch chan tea.Msg
}

// NextCmd returns a tea.Cmd that yields the next event from the stream, or
// RunStreamDoneMsg if the run has ended.
func (s *Stream) NextCmd() tea.Cmd {
	return func() tea.Msg {
		m, ok := <-s.ch
		if !ok {
			return RunStreamDoneMsg{}
		}
		return m
	}
}

// StartCmd launches a run in a goroutine and returns the stream plus the first
// drain command. The caller is expected to append NextCmd() to subsequent
// returned cmds in its Update loop until RunStreamDoneMsg arrives.
//
// StartCmd wires rc.ApplyNoteEdit and rc.ApplyNoteWrite to route through the
// stream: the tool emits an apply message, blocks on its Reply channel, and
// the main loop fills in the reply after touching the editor buffer.
func StartCmd(ctx context.Context, cfg llm.Config, rc *RunContext, opts RunOptions) (*Stream, tea.Cmd) {
	s := &Stream{ch: make(chan tea.Msg, 16)}
	emit := func(m tea.Msg) { s.ch <- m }

	rc.ApplyNoteEdit = func(ctx context.Context, old, new string, replaceAll bool) (NoteEditApplyReply, error) {
		reply := make(chan NoteEditApplyReply, 1)
		emit(NoteEditApplyMsg{Old: old, New: new, ReplaceAll: replaceAll, Reply: reply})
		select {
		case r := <-reply:
			return r, nil
		case <-ctx.Done():
			return NoteEditApplyReply{}, ctx.Err()
		}
	}
	rc.ApplyNoteWrite = func(ctx context.Context, content string) (NoteWriteApplyReply, error) {
		reply := make(chan NoteWriteApplyReply, 1)
		emit(NoteWriteApplyMsg{Content: content, Reply: reply})
		select {
		case r := <-reply:
			return r, nil
		case <-ctx.Done():
			return NoteWriteApplyReply{}, ctx.Err()
		}
	}
	rc.ApplyTodoWrite = func(ctx context.Context, texts []string) (TodoWriteApplyReply, error) {
		reply := make(chan TodoWriteApplyReply, 1)
		emit(TodoWriteApplyMsg{Texts: texts, Reply: reply})
		select {
		case r := <-reply:
			return r, nil
		case <-ctx.Done():
			return TodoWriteApplyReply{}, ctx.Err()
		}
	}
	rc.ApplyTodoDone = func(ctx context.Context, id int) (TodoDoneApplyReply, error) {
		reply := make(chan TodoDoneApplyReply, 1)
		emit(TodoDoneApplyMsg{ID: id, Reply: reply})
		select {
		case r := <-reply:
			return r, nil
		case <-ctx.Done():
			return TodoDoneApplyReply{}, ctx.Err()
		}
	}
	rc.ApplyNoteEditAt = func(ctx context.Context, edits map[string]string) (NoteEditAtApplyReply, error) {
		reply := make(chan NoteEditAtApplyReply, 1)
		emit(NoteEditAtApplyMsg{Edits: edits, Reply: reply})
		select {
		case r := <-reply:
			return r, nil
		case <-ctx.Done():
			return NoteEditAtApplyReply{}, ctx.Err()
		}
	}

	go func() {
		defer close(s.ch)
		Run(ctx, cfg, rc, opts, emit)
	}()
	return s, s.NextCmd()
}

// Run executes the agent loop synchronously, emitting events via emit().
// Returns when the run ends (finished, error, cancelled, or max steps).
func Run(ctx context.Context, cfg llm.Config, rc *RunContext, opts RunOptions, emit func(tea.Msg)) {
	if cfg.ProviderKind == "agent_sdk" {
		runAgentSDK(ctx, cfg, rc, opts, emit)
		return
	}
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

	client := llm.NewClient(cfg)
	msgs := []openai.ChatCompletionMessageParamUnion{
		openai.SystemMessage(sysPrompt),
	}
	msgs = append(msgs, transcript.ToAPIMessages(opts.TranscriptRows)...)
	msgs = append(msgs, openai.UserMessage(rc.SnapshotContent))

	// Inject pseudo-Read tool calls for user-provided file attachments.
	for _, fa := range opts.FileAttachments {
		toolID := fmt.Sprintf("att-%d", time.Now().UnixNano())
		msgs = append(msgs, pseudoReadCall(fa.Path, toolID))
		msgs = append(msgs, openai.ToolMessage(fa.Content, toolID))
	}

	// Live todo list — mutated by todo_write / todo_done apply replies so the
	// user message can be re-rendered between turns.
	liveTodos := append([]todos.Todo(nil), opts.Todos...)

	// Build user message — multimodal when images are pending. The active todo
	// list is appended to the prompt text so the model sees the current state.
	userMsg := buildUserMessage(userTextWithTodos(opts.UserPrompt, liveTodos), opts.PendingImages)
	msgs = append(msgs, userMsg)
	userMsgIdx := len(msgs) - 1

	tools := buildToolParams(mode, opts.AgentMode, opts.WriteScopeCount, opts.NoteWriteForbidden)
	start := time.Now()

	slog.Info("run_start", "mode", mode, "agent_mode", opts.AgentMode, "prompt_len", len(opts.UserPrompt), "todos", len(opts.Todos))
	emit(RunStartedMsg{})

	session, err := modellogs.Start(rc.ActivePath)
	if err != nil {
		slog.Warn("modellogs_open_failed", "error", err.Error())
	}
	defer session.Close()
	session.LogRunHeader(cfg.Model, mode)
	session.LogUserPrompt(opts.UserPrompt)

	for step := 0; step < maxSteps; step++ {
		if ctx.Err() != nil {
			slog.Info("run_finish", "reason", "cancelled")
			session.LogRunEnd(time.Since(start), "cancelled")
			emit(RunCancelledMsg{})
			return
		}

		session.NextStep()

		apiStart := time.Now()
		slog.Debug("api_request", "step", step, "model", cfg.Model, "messages", len(msgs))
		resp, err := client.Chat.Completions.New(ctx, openai.ChatCompletionNewParams{
			Model:    cfg.Model,
			Messages: msgs,
			Tools:    tools,
		})
		if err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				slog.Info("run_finish", "reason", "cancelled")
				session.LogRunEnd(time.Since(start), "cancelled")
				emit(RunCancelledMsg{})
				return
			}
			// If images caused the error, retry once without them.
			if len(opts.PendingImages) > 0 && isVisionError(err) {
				emit(VisionUnsupportedMsg{})
				msgs[userMsgIdx] = openai.UserMessage(userTextWithTodos(opts.UserPrompt, liveTodos))
				opts.PendingImages = nil
				resp, err = client.Chat.Completions.New(ctx, openai.ChatCompletionNewParams{
					Model:    cfg.Model,
					Messages: msgs,
					Tools:    tools,
				})
				if err != nil {
					if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
						slog.Info("run_finish", "reason", "cancelled")
						session.LogRunEnd(time.Since(start), "cancelled")
						emit(RunCancelledMsg{})
						return
					}
					slog.Error("api_error", "step", step, "error", err.Error(), "duration", time.Since(apiStart))
					session.LogRunEnd(time.Since(start), "api_error: "+err.Error())
					emit(RunErrorMsg{Message: err.Error()})
					return
				}
			} else {
				slog.Error("api_error", "step", step, "error", err.Error(), "duration", time.Since(apiStart))
				session.LogRunEnd(time.Since(start), "api_error: "+err.Error())
				emit(RunErrorMsg{Message: err.Error()})
				return
			}
		}

		if len(resp.Choices) == 0 {
			slog.Error("api_error", "step", step, "error", "no choices returned")
			session.LogRunEnd(time.Since(start), "api_error: no choices returned")
			emit(RunErrorMsg{Message: "model returned no choices"})
			return
		}

		choice := resp.Choices[0]
		slog.Info("api_response",
			"step", step,
			"input_tokens", resp.Usage.PromptTokens,
			"output_tokens", resp.Usage.CompletionTokens,
			"stop_reason", string(choice.FinishReason),
			"duration", time.Since(apiStart),
		)
		session.AddTokens(int(resp.Usage.PromptTokens), int(resp.Usage.CompletionTokens))
		session.LogThinking(extractThinking(resp.RawJSON()))
		msgs = append(msgs, choice.Message.ToParam())

		if len(choice.Message.ToolCalls) == 0 {
			if mode == ModeChat {
				if todos.AllDone(liveTodos) {
					emit(TodosClearedMsg{})
				}
				slog.Info("run_finish", "reason", "chat", "input_tokens", int(resp.Usage.PromptTokens), "output_tokens", int(resp.Usage.CompletionTokens), "elapsed", time.Since(start))
				session.LogChatResponse(choice.Message.Content)
				session.LogRunEnd(time.Since(start), "chat")
				emit(ChatFinishedMsg{
					Text:    choice.Message.Content,
					InTok:   int(resp.Usage.PromptTokens),
					OutTok:  int(resp.Usage.CompletionTokens),
					Elapsed: time.Since(start),
				})
				return
			}
			session.LogPlainText(choice.Message.Content)
			msgs = append(msgs, openai.UserMessage(plainTextNudge(choice.Message.Content)))
			continue
		}

		for _, call := range choice.Message.ToolCalls {
			name := call.Function.Name
			tool := Lookup(name)
			if tool == nil {
				slog.Warn("tool_unknown", "tool", name)
				emit(ToolResultMsg{Name: name, Summary: "unknown tool", IsError: true})
				msgs = append(msgs, openai.ToolMessage(
					fmt.Sprintf(`{"error":"unknown_tool","message":"no tool named %q"}`, name),
					call.ID,
				))
				continue
			}

			slog.Debug("tool_call", "tool", name, "args", previewArgs(call.Function.Arguments))
			session.LogToolCall(name, call.Function.Arguments)
			emit(ToolDispatchedMsg{Name: name, ArgsPreview: previewArgs(call.Function.Arguments)})
			result := tool.Execute(ctx, rc, call.Function.Arguments)
			msgs = append(msgs, openai.ToolMessage(result.JSON, call.ID))
			emit(ToolResultMsg{
				Name:       name,
				Summary:    result.Summary,
				IsError:    result.IsError,
				CallJSON:   call.Function.Arguments,
				ResultJSON: result.JSON,
			})
			session.LogToolResult(name, result.Summary, result.IsError)
			slog.Info("tool_result", "tool", name, "ok", !result.IsError, "summary", result.Summary)

			// If a tool mutated the active todo list, update our local copy
			// and re-render the user message so the next API call shows the
			// new checkbox state.
			if result.TodosAfter != nil {
				liveTodos = result.TodosAfter
				msgs[userMsgIdx] = buildUserMessage(userTextWithTodos(opts.UserPrompt, liveTodos), opts.PendingImages)
			}

			if result.EndsRun && !result.IsError {
				if todos.AllDone(liveTodos) {
					emit(TodosClearedMsg{})
				}
				slog.Info("run_finish", "reason", shortName(name), "input_tokens", int(resp.Usage.PromptTokens), "output_tokens", int(resp.Usage.CompletionTokens), "elapsed", time.Since(start))
				session.LogRunEnd(time.Since(start), shortName(name))
				emit(RunFinishedMsg{
					EndedOn: shortName(name),
					InTok:   int(resp.Usage.PromptTokens),
					OutTok:  int(resp.Usage.CompletionTokens),
					Elapsed: time.Since(start),
				})
				return
			}
		}
	}

	slog.Error("run_error", "message", "max steps reached")
	session.LogRunEnd(time.Since(start), "max steps reached")
	emit(RunErrorMsg{Message: "max steps reached"})
}

func buildToolParams(mode, agentMode string, writeScopeCount int, noteWriteForbidden bool) []openai.ChatCompletionToolUnionParam {
	var all []Tool
	switch agentMode {
	case "read":
		all = append(AllTools(), AgentReadTools()...)
	case "work":
		all = append(AllTools(), AgentWorkTools()...)
	default:
		all = AllTools()
	}
	scopeActive := mode == ModeNote && writeScopeCount > 0
	out := make([]openai.ChatCompletionToolUnionParam, 0, len(all)+1)
	for _, t := range all {
		if mode == ModeChat && (t.Name() == "note_edit" || t.Name() == "note_write") {
			continue
		}
		if scopeActive && (t.Name() == "note_edit" || t.Name() == "note_write") {
			continue
		}
		if noteWriteForbidden && t.Name() == "note_write" {
			continue
		}
		out = append(out, openai.ChatCompletionFunctionTool(shared.FunctionDefinitionParam{
			Name:        t.Name(),
			Description: param.NewOpt(t.Description()),
			Parameters:  shared.FunctionParameters(t.Schema()),
		}))
	}
	if scopeActive {
		out = append(out, openai.ChatCompletionFunctionTool(shared.FunctionDefinitionParam{
			Name:        "note_edit_at",
			Description: param.NewOpt(scopedEditDescription(writeScopeCount)),
			Parameters:  shared.FunctionParameters(scopedEditSchema(writeScopeCount)),
		}))
	}
	return out
}

// scopedEditSchema returns a JSON Schema whose `edits` object enumerates the
// exact slot keys present in the snapshot. Exposing the keys to the model
// lets it self-correct without needing to guess what's valid.
func scopedEditSchema(slotCount int) map[string]any {
	props := make(map[string]any, slotCount)
	for i := 1; i <= slotCount; i++ {
		props[fmt.Sprintf("%d", i)] = map[string]any{
			"type":        "string",
			"description": fmt.Sprintf("Content for slot #%d.", i),
		}
	}
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"edits": map[string]any{
				"type":                 "object",
				"description":          "Map of slot number (string key like \"1\") to new content. Omitted slots are left unchanged. Empty string deletes the slot's body but preserves its markers.",
				"properties":           props,
				"additionalProperties": false,
			},
		},
		"required":             []string{"edits"},
		"additionalProperties": false,
	}
}

func scopedEditDescription(slotCount int) string {
	slotWord := "slot"
	if slotCount != 1 {
		slotWord = "slots"
	}
	return fmt.Sprintf(
		"End the run by filling in the %d scoped-edit %s in the active note. The note contains <!--Write #N location--> markers (insert new text here) and <!--Rewrite #N start-->...<!--Rewrite #N end--> markers (replace the existing body). Submit content for any subset of slots; omitted slots are left unchanged. The @>> <<@ wrapper markers in the raw note are preserved automatically.",
		slotCount, slotWord,
	)
}

func previewArgs(args string) string {
	args = strings.ReplaceAll(args, "\n", " ")
	const max = 60
	if len(args) > max {
		return args[:max] + "…"
	}
	return args
}

func shortName(toolName string) string {
	switch toolName {
	case "note_edit":
		return "edit"
	case "note_write":
		return "write"
	case "note_edit_at":
		return "edit_at"
	default:
		return toolName
	}
}

// pseudoReadCall builds a synthetic assistant message that looks like the model
// called the "Read" tool — used to present user-attached files as already-read
// context before the user's actual message.
func pseudoReadCall(filePath, toolID string) openai.ChatCompletionMessageParamUnion {
	argsJSON, _ := json.Marshal(map[string]string{"file_path": filePath})
	assistant := openai.ChatCompletionAssistantMessageParam{
		ToolCalls: []openai.ChatCompletionMessageToolCallUnionParam{{
			OfFunction: &openai.ChatCompletionMessageFunctionToolCallParam{
				ID: toolID,
				Function: openai.ChatCompletionMessageFunctionToolCallFunctionParam{
					Name:      "Read",
					Arguments: string(argsJSON),
				},
			},
		}},
	}
	return openai.ChatCompletionMessageParamUnion{OfAssistant: &assistant}
}

// userTextWithTodos returns the user prompt text with the active todos block
// appended when there are any. The runner re-renders this whenever liveTodos
// changes so the next API call shows current state.
func userTextWithTodos(prompt string, items []todos.Todo) string {
	block := todos.PromptBlock(items)
	if block == "" {
		return prompt
	}
	if prompt == "" {
		return block
	}
	return prompt + "\n\n" + block
}

// buildUserMessage returns a text-only or multimodal user message depending on
// whether images are provided.
func buildUserMessage(text string, images [][]byte) openai.ChatCompletionMessageParamUnion {
	if len(images) == 0 {
		return openai.UserMessage(text)
	}
	parts := []openai.ChatCompletionContentPartUnionParam{
		openai.TextContentPart(text),
	}
	for _, img := range images {
		encoded := "data:image/png;base64," + base64.StdEncoding.EncodeToString(img)
		parts = append(parts, openai.ImageContentPart(
			openai.ChatCompletionContentPartImageImageURLParam{URL: encoded},
		))
	}
	return openai.UserMessage(parts)
}

// extractThinking parses reasoning_content from the raw API response JSON.
// Returns an empty string when the field is absent or parsing fails.
func extractThinking(rawJSON string) string {
	var raw struct {
		Choices []struct {
			Message struct {
				ReasoningContent string `json:"reasoning_content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if json.Unmarshal([]byte(rawJSON), &raw) == nil && len(raw.Choices) > 0 {
		return raw.Choices[0].Message.ReasoningContent
	}
	return ""
}

// isVisionError reports whether an API error is likely caused by the model not
// supporting image content.
func isVisionError(err error) bool {
	s := strings.ToLower(err.Error())
	for _, kw := range []string{"vision", "image", "multimodal", "unsupported_media_type"} {
		if strings.Contains(s, kw) {
			return true
		}
	}
	return false
}
