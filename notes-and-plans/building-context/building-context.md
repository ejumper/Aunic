# Building the Model-Context (aka "message" block)
The `model-context` is the json formatted "messages" section of the API call sent to the model. While everything I'm talking about here belongs in the "messages" block of the API, I am referring to it here as `model-context` to avoid confusion with the typical "message" block formatting.
- 2 different message block formattings are needed. The Anthropic style for Claude models, and the OpenAI compatible style for all other models.
the `model-context` is split into 3 parts...
- `transcript` + `note-snapshot` + `user-prompt` = `model-context`
(note: remember you are building Anthropic and OpenAI compatible APIs, so despite the API being setup differently, in the end they should be formatted to maximize compatibility with what the model server expects to see)

## Building the `note-snapshot` and `user-prompt`
The `note-snapshot` and `user-prompt` are combined into a single `role: "user"` message at the end of the `model-context`. They cannot be sent as separate user messages because both the Anthropic API and many OpenAI-compatible providers reject consecutive same-role messages.

### `note-snapshot`
This is the plain-text "context" that the user and the model are creating/editing in `note-mode`.
- It is created by...
    1. taking a "snapshot" of the `note-content`
    2. parsing any edit commands inside it per `notes-and-plans/building-context/parsing-edit-commands.md`
    3. creating a `note-snapshot` from the parsed results

### `user-prompt`
This is the text inside the `user-prompt-editor` at the time `run` is sent.

### Combined message
The `note-snapshot` is placed first and the `user-prompt` last, separated by a delimiter:
- {"role": "user", "content": "<note-snapshot>\n\n---\n\n<user-prompt>"}
- The `user-prompt` is always at the bottom since models pay the most attention to the end of the message.

## Building the `transcript`
The `transcript` must be translated from the markdown table formatting to json to be sent to the model.
- it must be built in the API format compatible with the model being used.
- details on how this is done is outlined in `notes-and-plans/building-context/transcript-to-api.md`
