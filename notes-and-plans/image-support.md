# Image Support

## 1. How `/home/ejumps/HalfaCloud/Backups/coding-agent-program-example` handles images

The reference app supports images in two distinct ways: prompt-local image attachments and persistent file attachments.

### Prompt-local images

- The prompt input pipeline accepts pasted image content in [processUserInput.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/utils/processUserInput/processUserInput.ts).
- Image pastes are filtered, assigned `imagePasteIds`, converted into structured image blocks, and resized/downsampled before send with `maybeResizeAndDownsampleImageBlock(...)`.
- Those blocks are then appended to the current user turn in [processTextPrompt.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/utils/processUserInput/processTextPrompt.ts), so the provider receives multimodal content, not a base64 blob stuffed into plain text.
- The app also stores pasted images through [imageStore.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/utils/imageStore.ts) so the UI/transcript layer can keep a stable reference to what was attached.

### Persistent file attachments

- The app also has a general attachment pipeline in [attachments.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/utils/attachments.ts).
- When an attached file is an image, [FileReadTool.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/tools/FileReadTool/FileReadTool.ts) routes it through `readImageWithTokenBudget(...)`.
- That image path reads the file, validates the format, resizes/compresses it, and returns structured image content including media type and base64 data.
- At send time, [messages.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/utils/messages.ts) normalizes attachments into provider-facing messages. For image files, it preserves image blocks rather than flattening them into text.

### Important design traits

- The app does not treat images as text snapshots. It sends them as structured multimodal content.
- It preprocesses images before send so they fit model/API limits. See [imageResizer.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/utils/imageResizer.ts).
- It keeps prompt-local images separate from persistent attachments.
- It also accounts for images in token estimation with special handling in [tokenEstimation.ts](/home/ejumps/HalfaCloud/Backups/coding-agent-program-example/src/services/tokenEstimation.ts).

### What Aunic should borrow from this

- Aunic should copy the separation between persistent image context and prompt-local image attachments.
- Aunic should preprocess images before provider send rather than trying to pass raw originals through untouched.
- Aunic should normalize image inputs into provider-native structured blocks at send time instead of forcing them through the text note snapshot path.
- Aunic should not assume that every provider or SDK uses Anthropic's exact wire format, even when the underlying capability is similar.

## 2. How Aunic should implement image attachments

Aunic should support images as first-class multimodal inputs, not as text. The current note snapshot pipeline is text-only and should stay text-only. Images should travel alongside that pipeline as separate provider input blocks.

### Recommended model

Support two image sources:

1. Persistent included images
- These should behave like included files conceptually.
- They should be resolved on every prompt send for the active source note.
- They should remain in context across future prompts until excluded.

2. Ephemeral prompt attachments
- These should be attached only to the current submitted prompt.
- They should not remain in future context after the turn completes.

### Why the current Aunic architecture is not ready yet

- `ProviderRequest` is text-centric in [domain.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/domain.py): `note_snapshot` and `user_prompt` are strings, and `Message.content` is also just a string.
- The context builder in [engine.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/context/engine.py) assumes included files become text note snapshots.
- The file reader in [file_manager.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/context/file_manager.py) reads UTF-8 text, so direct image includes would currently fail.
- The browser prompt submit path in [requests.ts](/home/ejumps/HalfaCloud/Aunic/web/src/ws/requests.ts) and [connection.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/browser/connection.py) has no attachment payload.

### Recommended implementation in Aunic

#### A. Add first-class image inputs to the backend request model

Extend the request model so provider calls can carry images separately from text. For example:

- `persistent_images: list[ProviderImageInput]`
- `prompt_images: list[ProviderImageInput]`

Each image input should carry at least:

- source path
- media type
- processed bytes or base64 payload
- width/height
- whether it is persistent or ephemeral

This should be added to [domain.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/domain.py), not jammed into `note_snapshot`.

#### B. Keep text note snapshots text-only

- Markdown/text files should keep flowing through the existing note snapshot path.
- Image files should be resolved separately and appended as structured multimodal inputs at provider translation time.
- This preserves the current note snapshot model and avoids polluting transcript text with binary data.

#### C. Add an image asset loader/preprocessor

Aunic already has Pillow available and already has metadata-only image reading in [filesystem.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/tools/filesystem.py). It should add a real image attachment loader that:

- validates supported image types
- reads dimensions/media type
- resizes/compresses to model-safe bounds
- emits provider-ready image content

This should be a dedicated helper, not an overload of `read_snapshot()`.

#### D. Persistent images should integrate with include state

- The include persistence layer in [file_ui_state.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/file_ui_state.py) can already store arbitrary paths for direct includes.
- That should be extended so included image files are resolved into `persistent_images` during prompt send.
- Recursive include directories should also be expanded beyond only `.md` files when image support is enabled.
- Text includes should still feed note snapshot context; image includes should feed persistent multimodal inputs.

#### E. Ephemeral images should be part of `submit_prompt`

The browser submit flow should be extended so the prompt send carries selected/pasted images for only the current turn:

- [web/src/ws/requests.ts](/home/ejumps/HalfaCloud/Aunic/web/src/ws/requests.ts)
- [src/aunic/browser/messages.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/browser/messages.py)
- [src/aunic/browser/connection.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/browser/connection.py)
- [src/aunic/browser/session.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/browser/session.py)
- [web/src/state/prompt.ts](/home/ejumps/HalfaCloud/Aunic/web/src/state/prompt.ts)
- [web/src/ws/types.ts](/home/ejumps/HalfaCloud/Aunic/web/src/ws/types.ts)

These images should be used only for the current provider call and then discarded from future context.

#### F. Provider adapters should translate images natively

This should not be one generic code path for every provider. The concepts are similar across providers, but the wire formats and transport capabilities are not identical enough to share one formatter.

Recommended transport families:

- `anthropic_messages`
- `claude_agent_sdk_streaming`
- `openai_responses`
- optionally `openai_chat_vision` for compatible providers that use the older chat-style multimodal format
- `unsupported`

How those families differ:

- Anthropic Messages API is the cleanest first implementation target. It supports mixed `text` and `image` content blocks in a user message, with image sources such as base64, URL, or file reference. Aunic's [claude.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/providers/claude.py) is already structurally closest to this model.
- Claude Agent SDK / Claude Code SDK is similar in concept but not identical in transport behavior. The SDK docs distinguish streaming input mode from single-message mode, and direct image attachments are supported in streaming mode but not in the simpler single-message path. Aunic currently uses the SDK in [claude_client.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/providers/claude_client.py), so image support there should be implemented against the SDK mode Aunic actually uses, not assumed to be identical to raw Anthropic Messages.
- OpenAI native APIs also support image input, but they use OpenAI's own payload shapes rather than Anthropic's block format. So these should share Aunic's internal attachment model, but not the final request serializer.
- OpenAI-compatible providers need profile-specific handling in [shared.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/providers/shared.py), because “OpenAI-compatible” does not guarantee one uniform multimodal schema. Some may support Responses-style image input, some may support chat-style vision payloads, and some may be text-only.
- Codex should be treated as unsupported for now, because [codex_client.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/providers/codex_client.py) currently sends only text input and explicitly starts sessions with `view_image: False`.

So the right design is:

- one internal Aunic image attachment model
- one serializer/adapter per transport family
- explicit capability gating per model/profile

That avoids duplicating all image-processing logic while still respecting the real transport differences.

### Recommended capability/refusal behavior

Aunic should explicitly track whether the selected model/provider supports image input.

Recommended fields:

- `supports_images: bool`
- `image_transport: str`

These likely belong in:

- [model_options.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/model_options.py)
- [proto_settings.py](/home/ejumps/HalfaCloud/Aunic/src/aunic/proto_settings.py)

For example:

- Claude API profiles: `supports_images: true`, `image_transport: "anthropic_messages"`
- Claude SDK profiles: `supports_images: true`, `image_transport: "claude_agent_sdk_streaming"` only if Aunic is using the streaming-capable path
- OpenAI native profiles: `supports_images: true`, `image_transport: "openai_responses"`
- OpenAI-compatible profiles: explicitly configured per profile rather than inferred
- Codex: `supports_images: false`, `image_transport: "unsupported"`

Graceful refusal should happen in two places:

1. Frontend/UI
- If the selected model does not support images, the browser should disable or visibly reject image attachments before send.

2. Backend/session guard
- The backend should still enforce a final check before running the request.
- If image attachments are present and the model does not support them, return a clean user-facing error such as:
  - `Selected model does not support image attachments in Aunic yet. Remove the image attachments or switch models.`

That is especially important for:

- Codex
- llama.cpp profiles that are text-only
- OpenAI-compatible profiles without multimodal support configured

### Transcript/context behavior

- Persistent image includes should not be serialized into note transcript text.
- Ephemeral prompt images should not remain in context after the turn.
- If Aunic wants transcript/debug visibility later, it should store only lightweight metadata for image attachments such as path, hash, and media type, not raw base64 payloads.

### Practical rollout order

Recommended implementation order:

1. Add backend image input types to `ProviderRequest`.
2. Add image preprocessing/loading.
3. Add browser prompt-local image attachments.
4. Add Claude provider support first.
5. Add capability gating and graceful refusal.
6. Add persistent image includes through the include/project-files system.
7. Add other provider integrations only where image support is explicitly known.

### Bottom line

The clean way to add image support to Aunic is:

- keep note snapshot context text-only
- send images as separate multimodal inputs
- support both persistent include-based images and ephemeral prompt-only images
- preprocess images before send
- gate the feature per model/provider and refuse cleanly when unsupported

That follows the successful direction used by the reference app, while fitting Aunic’s existing note/include architecture more cleanly than trying to force images through the text snapshot pipeline.
