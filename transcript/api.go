package transcript

import (
	openai "github.com/charmbracelet/openai-go"
)

// ToAPIMessages translates rows to OpenAI chat messages, preserving order so
// tool_call → tool_result pairs stay adjacent in the conversation. Rows it
// can't interpret are skipped silently.
//
// Mapping:
//   - Role=user,      Type=message     → UserMessage(text)
//   - Role=assistant, Type=message     → AssistantMessage(text)
//   - Role=assistant, Type=tool_call   → AssistantMessage with a single tool_call
//   - Role=tool,      Type=tool_result → ToolMessage(content_json, tool_id)
func ToAPIMessages(rows []Row) []openai.ChatCompletionMessageParamUnion {
	out := make([]openai.ChatCompletionMessageParamUnion, 0, len(rows))
	for _, r := range rows {
		switch {
		case r.Role == RoleUser && r.Type == TypeMessage:
			msg, err := DecodeMessage(r.Content)
			if err != nil {
				continue
			}
			out = append(out, openai.UserMessage(msg.Text))

		case r.Role == RoleAssistant && r.Type == TypeMessage:
			msg, err := DecodeMessage(r.Content)
			if err != nil {
				continue
			}
			out = append(out, openai.AssistantMessage(msg.Text))

		case r.Role == RoleAssistant && r.Type == TypeToolCall:
			assistant := openai.ChatCompletionAssistantMessageParam{
				ToolCalls: []openai.ChatCompletionMessageToolCallUnionParam{
					{
						OfFunction: &openai.ChatCompletionMessageFunctionToolCallParam{
							ID: r.ToolID,
							Function: openai.ChatCompletionMessageFunctionToolCallFunctionParam{
								Name:      r.Tool,
								Arguments: string(r.Content),
							},
						},
					},
				},
			}
			out = append(out, openai.ChatCompletionMessageParamUnion{OfAssistant: &assistant})

		case r.Role == RoleTool && r.Type == TypeToolResult:
			out = append(out, openai.ToolMessage(string(r.Content), r.ToolID))
		}
	}
	return out
}
