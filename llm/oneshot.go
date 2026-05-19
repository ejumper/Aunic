package llm

import (
	"context"
	"fmt"

	openai "github.com/charmbracelet/openai-go"
)

// OneShotChat sends a single non-streaming completion request with no tools
// and returns the assistant's text response. Intended for one-off model
// utilities like /chat2note step 1 (structuring the transcript) where the
// caller needs plain text out and does not want to go through the full
// streaming run loop in runner/.
//
// sysPrompt is sent as the system message; msgs are appended after it. The
// caller is responsible for assembling the conversation messages — typically
// via transcript.ToAPIMessages for chat history plus a final user message
// stating the immediate task.
func OneShotChat(ctx context.Context, cfg Config, sysPrompt string, msgs []openai.ChatCompletionMessageParamUnion) (string, error) {
	client := NewClient(cfg)
	full := make([]openai.ChatCompletionMessageParamUnion, 0, len(msgs)+1)
	full = append(full, openai.SystemMessage(sysPrompt))
	full = append(full, msgs...)
	resp, err := client.Chat.Completions.New(ctx, openai.ChatCompletionNewParams{
		Model:    cfg.Model,
		Messages: full,
	})
	if err != nil {
		return "", err
	}
	if len(resp.Choices) == 0 {
		return "", fmt.Errorf("model returned no choices")
	}
	return resp.Choices[0].Message.Content, nil
}
