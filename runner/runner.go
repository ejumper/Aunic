package runner

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	openai "github.com/charmbracelet/openai-go"
	"github.com/charmbracelet/openai-go/packages/param"
	"github.com/charmbracelet/openai-go/shared"

	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/transcript"
)

const maxSteps = 10

// ModeNote and ModeChat select runner behavior on plain-text responses and
// available tool names. ModeNote (the original behavior) nudges the model
// back to calling note_edit/note_write; ModeChat accepts plain text as the
// run's terminal reply and excludes the note-mutating tools from the API
// tool list.
const (
	ModeNote = "note"
	ModeChat = "chat"
)

const noteSystemPrompt = `You are aunic, a note-based AI agent. The user's active markdown note is sent
to you first, followed by their request. Always end your run by calling
note_edit or note_write to update the note — do not reply with plain text.
You may use web_search and web_fetch to gather information before editing.`

const chatSystemPrompt = `You are aunic, a note-based AI assistant in chat mode.
The user's active markdown note is provided as context, followed by their request.
Reply with a plain-text message — do not call note_edit or note_write (they are
not available in this mode). You may use web_search and web_fetch to gather
information before replying.`

// RunOptions carries everything Run needs beyond the shared LLM config and the
// RunContext.
type RunOptions struct {
	Mode           string // ModeNote or ModeChat; empty defaults to ModeNote
	UserPrompt     string
	TranscriptRows []transcript.Row
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

	go func() {
		defer close(s.ch)
		Run(ctx, cfg, rc, opts, emit)
	}()
	return s, s.NextCmd()
}

// Run executes the agent loop synchronously, emitting events via emit().
// Returns when the run ends (finished, error, cancelled, or max steps).
func Run(ctx context.Context, cfg llm.Config, rc *RunContext, opts RunOptions, emit func(tea.Msg)) {
	mode := opts.Mode
	if mode == "" {
		mode = ModeNote
	}
	sysPrompt := noteSystemPrompt
	if mode == ModeChat {
		sysPrompt = chatSystemPrompt
	}

	client := llm.NewClient(cfg)
	msgs := []openai.ChatCompletionMessageParamUnion{
		openai.SystemMessage(sysPrompt),
	}
	msgs = append(msgs, transcript.ToAPIMessages(opts.TranscriptRows)...)
	msgs = append(msgs,
		openai.UserMessage(rc.SnapshotContent),
		openai.UserMessage(opts.UserPrompt),
	)
	tools := buildToolParams(mode)
	start := time.Now()

	emit(RunStartedMsg{})

	for step := 0; step < maxSteps; step++ {
		if ctx.Err() != nil {
			emit(RunCancelledMsg{})
			return
		}

		resp, err := client.Chat.Completions.New(ctx, openai.ChatCompletionNewParams{
			Model:    cfg.Model,
			Messages: msgs,
			Tools:    tools,
		})
		if err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				emit(RunCancelledMsg{})
				return
			}
			emit(RunErrorMsg{Message: err.Error()})
			return
		}

		if len(resp.Choices) == 0 {
			emit(RunErrorMsg{Message: "model returned no choices"})
			return
		}

		choice := resp.Choices[0]
		msgs = append(msgs, choice.Message.ToParam())

		if len(choice.Message.ToolCalls) == 0 {
			if mode == ModeChat {
				emit(ChatFinishedMsg{
					Text:    choice.Message.Content,
					InTok:   int(resp.Usage.PromptTokens),
					OutTok:  int(resp.Usage.CompletionTokens),
					Elapsed: time.Since(start),
				})
				return
			}
			msgs = append(msgs, openai.UserMessage(plainTextNudge(choice.Message.Content)))
			continue
		}

		for _, call := range choice.Message.ToolCalls {
			name := call.Function.Name
			tool := Lookup(name)
			if tool == nil {
				emit(ToolResultMsg{Name: name, Summary: "unknown tool", IsError: true})
				msgs = append(msgs, openai.ToolMessage(
					fmt.Sprintf(`{"error":"unknown_tool","message":"no tool named %q"}`, name),
					call.ID,
				))
				continue
			}

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

			if result.EndsRun && !result.IsError {
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

	emit(RunErrorMsg{Message: "max steps reached"})
}

func buildToolParams(mode string) []openai.ChatCompletionToolUnionParam {
	all := AllTools()
	out := make([]openai.ChatCompletionToolUnionParam, 0, len(all))
	for _, t := range all {
		if mode == ModeChat && (t.Name() == "note_edit" || t.Name() == "note_write") {
			continue
		}
		out = append(out, openai.ChatCompletionFunctionTool(shared.FunctionDefinitionParam{
			Name:        t.Name(),
			Description: param.NewOpt(t.Description()),
			Parameters:  shared.FunctionParameters(t.Schema()),
		}))
	}
	return out
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
	default:
		return toolName
	}
}
