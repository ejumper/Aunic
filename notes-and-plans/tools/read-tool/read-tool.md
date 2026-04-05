## The Read Tool
The model uses `read` in `work-mode` to read files from the local filesystem.
- `read`
    - `file_path`
        - required string
        - the file to read
    - `offset`
        - optional integer
        - the starting line number for text-file reads
    - `limit`
        - optional integer
        - the number of lines to read for text-file reads
    - `pages`
        - optional string
        - a PDF page range like `"1-5"` or `"3"`

(note: generic `tool_id` handling and in-memory vs persisted tool history already live in `notes-and-plans/tools/tools.md`)
(note: transcript row encoding/rendering already lives in `notes-and-plans/active-markdown-note/active-markdown-note.md`)
(note: transcript-to-provider translation already lives in `notes-and-plans/building-context/transcript-to-api.md`)

## Design Goal
Make Aunic's `read` tool behave as close as possible to the implementation in `coding-agent-program-example`, while retaining Aunic-specific architecture:
- `tool_call` and `tool_result` / `tool_error` rows are persisted in the `transcript`
- `tool_result` and `tool_error` remain distinct row types in the `transcript`
- Aunic can store structured JSON in transcript rows instead of flattening everything immediately to provider-facing strings or blocks

The key idea is:
- internally, `read` should be a structured multi-type file reader
- the `transcript` should persist structured JSON describing the read result
- transcript-to-provider translation can then format that structured result into numbered text, image blocks, document blocks, or stub strings as needed by the model API

## What `read` Is For
`read` is for reading a known file path directly.
- use `read` when the model already knows the specific file it wants
- use `read` for text files, images, PDFs, and notebooks
- use `grep`, `glob`, or `list` when the model is still searching

Like the example implementation:
- `read` should be read-only
- `read` should be concurrency-safe
- `read` should be preferred over shelling out to `cat`, `head`, or `tail`

## How A `read` Tool Call Starts
1. **The model emits a tool call**
    - The provider returns an assistant message containing a `read` tool call.
    - The API, not Aunic, provides the `tool_id`.
    - The arguments must contain `file_path`.

2. **Aunic parses and validates the arguments**
    - `file_path` must exist and must be a string
    - `offset`, if present, must be an integer
    - `limit`, if present, must be an integer
    - `pages`, if present, must be a string

3. **Aunic records the `tool_call` row**
    - The `tool_call` should be added to the in-memory message list immediately.
    - Because `read` is a persistent `work-mode` tool, it should also be written to the `transcript`.
    - The `tool_call` row should preserve the raw provider-emitted arguments.

## Input Normalization
After parsing, Aunic should normalize the effective input used for validation, permissions, and execution.

### Path normalization
- if `file_path` is relative, resolve it relative to the current project working directory
- if `file_path` uses `~`, expand it
- keep the original `tool_call` row untouched, but use the normalized absolute path for all internal checks

### Range semantics
For text reads:
- `offset` is the starting line number exposed to the model
- if omitted, `offset` defaults to `1`
- `limit` is optional; if omitted, the tool reads from `offset` to the tool's normal cap

For PDF reads:
- `pages` is only meaningful for PDF files
- `pages` uses 1-indexed page ranges like `"1-5"`, `"3"`, or `"10-20"`

## Prompt Expectations
To stay close to the example's prompt behavior, the `read` tool description should explicitly say:
- `file_path` should be absolute
- by default the tool reads from the start of the file
- `offset` and `limit` should be used for larger files or targeted reads
- text results are returned with line numbers
- this tool can read images, PDFs, and Jupyter notebooks
- this tool cannot read directories
- screenshot paths should be read with this tool, not with another tool

## Validation And Safety Checks
Before doing the actual read, Aunic should stay close to the example's validation flow.

### PDF page-range validation
If `pages` is present:
- parse it before any filesystem I/O
- reject invalid ranges with a `tool_error`
- reject ranges that exceed the configured maximum pages per request

### Deny-rule validation
Before any read I/O:
- normalize the path
- check whether the path is already denied by read-permission settings
- if so, reject with a `tool_error`

### UNC-path safety
To stay close to the example's behavior:
- if the path is a UNC/network path form, do not do filesystem I/O before permissions are resolved
- let the permission system decide first

### Binary-file validation
Like the example:
- reject unsupported binary file types early
- allow the file types the tool knows how to render natively, especially images and PDFs

### Block dangerous device files
The tool should reject device files that would hang or produce infinite output.
- examples: `/dev/zero`, `/dev/random`, `/dev/stdin`, `/dev/tty`, and similar aliases
- reject them with a `tool_error`

## Permission Flow
After validation, `read` goes through the shared read-permission system.

### What `read` permissions match against
To stay close to the example:
- permission checks should match the actual normalized target path
- permission checks should also consider symlink-resolved variants of that path

This means read permissions are path-based, not transcript-based and not artificial project-root scoped.

### Ask / Allow / Deny
If the permission result is `ask`, Aunic should pause execution and show a permission prompt in the UI.
- `once`
    - allow this one read only
- `always`
    - allow future matching reads for the rest of the current Aunic session
- `reject`
    - deny the request

### Outside working directory
If the file is outside the normal working directory:
- the shared read-permission rules should decide whether it is allowed automatically or must be asked
- suggestion behavior can stay close to the example by recommending session-scoped directory allowances rather than broad unrelated access

## Read Limits
The example uses two distinct caps for text-style reads. Aunic should copy that design.

### Byte cap
There should be a maximum read-size cap in bytes.
- by default this applies to the whole file for ordinary text reads
- if the file is too large, the read should fail with a `tool_error` that tells the model to use `offset` / `limit` or a more targeted tool

### Token cap
After content is read, Aunic should estimate token count.
- if the estimate is low enough, skip the expensive exact count
- if the estimate is high, compute a more accurate count
- if the content exceeds the configured max output tokens, fail with a `tool_error`

This should happen after the read, like the example.

### Important large-file behavior
To stay close to the example:
- when `limit` is omitted, byte-size caps can reject a large text read before returning content
- when `limit` is provided, the read path should focus on the selected range rather than blindly rejecting on total file size

## Re-Read Deduplication
The example deduplicates repeated reads of the same range when the file has not changed. Aunic should do the same.

If:
- the same normalized path was already read earlier in the run
- the same `offset` / `limit` range is requested again
- the file's current modification time matches the stored read state

Then:
- do not resend the full content
- return a `tool_result` of type `file_unchanged`
- the provider-facing translation can emit the same stub string as the example: the earlier read result is still current

This keeps repeated reads from bloating context.

## Type-Specific Read Behavior
The example's read tool supports multiple output modes. Aunic should keep the same conceptual split.

### Text files
For normal text files:
- read line-oriented content
- return raw text content plus metadata like `start_line`, `num_lines`, and `total_lines`
- track the file's modification time in read state

The actual reading implementation should stay close to the example:
- use a fast whole-file path for smaller regular files
- use a streaming path for larger files and non-regular cases
- strip BOM and normalize CRLF to LF for returned text content

### Images
For image files:
- read the file once
- resize/downsample/compress as needed to fit token limits
- return structured image metadata

Like the example:
- the read tool should be able to serve screenshot files directly
- zero-byte image files should fail with a `tool_error`

### Notebooks
For `.ipynb` files:
- read the notebook cells as structured JSON
- apply byte/token limits to the notebook JSON representation
- store the read in read-state so later edit/write tools know the notebook was read

### PDFs
For PDFs:
- if `pages` is provided, read only that page range
- if the PDF is too large to read at once, reject and tell the model to use `pages`
- if full-PDF reading is unsupported in the active setup/model, reject with a helpful `tool_error`

Like the example:
- a page-range read can return an extracted-pages result rather than one giant blob
- a full-PDF read can produce document-style provider output

### Directories
The tool cannot read directories.
- if the path resolves to a directory, fail with a `tool_error`
- tell the model to use `list` or `bash` for directories

## Friendly Not-Found Handling
The example adds user-helpful not-found messaging. Aunic should keep that behavior.

If the file does not exist:
- try any safe path-normalization fallback that the platform-specific implementation needs
- if the file is still missing, return a `tool_error`
- include the current working directory note in the message
- if possible, suggest a nearby path or similar filename

This is especially useful when the model used a nearly-correct relative path.

## Read-State Tracking
`read` should update Aunic's session read-state because later `edit` / `write` behavior depends on it.

For successful reads, store:
- normalized path
- returned content or notebook JSON representation
- file modification time
- `offset`
- `limit`

This read-state is what later tools use to decide:
- whether the file has been read at all
- whether the read was full or partial
- whether the file has changed since it was read

## Read Execution
If validation and permissions pass, Aunic should perform the read in this order.

1. **Resolve effective limits**
    - compute the active byte and token caps
    - allow per-context overrides if Aunic exposes them

2. **Normalize the target path**
    - expand the incoming path to the effective internal absolute path

3. **Attempt dedup**
    - if this exact range was already read and the file is unchanged, return `file_unchanged`

4. **Dispatch by file type**
    - notebook
    - image
    - PDF
    - text

5. **Apply byte/token checks**
    - reject oversized results with a `tool_error`

6. **Update read-state**
    - store content/range/timestamp for future edit/write checks

7. **Build the structured result**
    - store the structured output object

8. **Persist the `tool_result` row**
    - add it to the in-memory message list
    - add it to the `transcript`

## Result Formatting In The `transcript`
To stay close to the example while preserving Aunic's transcript architecture, the persisted `tool_result` content for `read` should be structured JSON.

### Text result shape
```json
{
  "type": "text",
  "file": {
    "file_path": "/path/to/project/src/file.py",
    "content": "raw file contents without line numbers",
    "num_lines": 120,
    "start_line": 1,
    "total_lines": 120
  }
}
```

### File-unchanged result shape
```json
{
  "type": "file_unchanged",
  "file": {
    "file_path": "/path/to/project/src/file.py"
  }
}
```

### Notebook result shape
```json
{
  "type": "notebook",
  "file": {
    "file_path": "/path/to/project/notebook.ipynb",
    "cells": [
      {}
    ]
  }
}
```

### Image / PDF / extracted-pages result shape
Because Aunic stores transcript history in a markdown table inside the note file, large base64 payloads should not be dumped directly into transcript rows unless that proves acceptable in practice.

For media-heavy results, Aunic should store:
- compact structured metadata in the transcript row
- a cache key or cache reference to any large binary payload stored outside the markdown table

Suggested image shape:
```json
{
  "type": "image",
  "file": {
    "file_path": "/path/to/image.png",
    "media_type": "image/png",
    "original_size": 12345,
    "dimensions": {
      "original_width": 1000,
      "original_height": 800,
      "display_width": 1000,
      "display_height": 800
    },
    "cache_key": "read-result-image-abc123"
  }
}
```

Suggested PDF shape:
```json
{
  "type": "pdf",
  "file": {
    "file_path": "/path/to/file.pdf",
    "original_size": 543210,
    "cache_key": "read-result-pdf-abc123"
  }
}
```

Suggested extracted-pages shape:
```json
{
  "type": "parts",
  "file": {
    "file_path": "/path/to/file.pdf",
    "original_size": 543210,
    "count": 5,
    "cache_keys": [
      "read-result-pdf-page-1",
      "read-result-pdf-page-2"
    ]
  }
}
```

This is the main Aunic-specific departure from the example's direct provider-block return shape.

## Why The `transcript` Should Store Structured Read Results
This keeps Aunic compatible with the markdown-table transcript and still close to the example.
- text results can be stored directly as JSON
- notebook results can be stored directly as JSON if within normal limits
- image/PDF payloads can be represented by compact JSON metadata plus cache references
- transcript-to-provider translation can rehydrate the structured result into the provider's expected format later

## Provider-Facing Translation
When Aunic translates transcript rows back into API messages, it should format structured read results the way the example does.

### Text translation
For text results:
- add line numbers at translation time
- if the content is non-empty, return the numbered text
- if the file exists but the returned slice is empty because the file is empty, return a warning/reminder string
- if the file exists but `offset` is beyond the file length, return a warning/reminder string
- if Aunic adopts the same malware-analysis reminder, append it at translation time rather than persisting it into the transcript row

### File-unchanged translation
For `file_unchanged`:
- return the stub string telling the model the earlier read result is still current

### Image / PDF translation
For media results:
- rehydrate the cached payload
- build the provider-specific image/document blocks at translation time

This keeps the transcript compact and provider-agnostic.

## What Counts As A Failure
Not every unsuccessful read attempt is the same kind of failure.

### `tool_error`
Use `tool_error` when Aunic itself could not or would not perform the read.
- malformed arguments
- missing `file_path`
- invalid `pages`
- denied path
- unsupported binary file type
- blocked device file
- path is a directory
- file not found
- file exceeds size or token limits
- PDF requires page targeting
- PDF/image/notebook read failure
- internal execution failure before a usable success result could be produced

Suggested `tool_error` content format:
```json
{
  "category": "read_failed",
  "reason": "file_not_found",
  "file_path": "/path/to/project/src/file.py",
  "message": "File does not exist. Note: your current working directory is /path/to/project."
}
```

### `tool_result`
Use `tool_result` for successful reads.
- successful text read
- successful image read
- successful PDF read
- successful notebook read
- successful `file_unchanged` dedup result

## Returning The Result To The Model
After the result object is built:
1. write the `tool_result` or `tool_error` row to the in-memory message list
2. write the same row to the `transcript`
3. translate the row into the provider-specific tool-result shape described in `notes-and-plans/building-context/transcript-to-api.md`
4. prompt the model for its next step

## Transcript Rendering Implications
To stay close to the example's UI behavior, the Aunic transcript renderer should not dump full read payloads inline by default.

For human rendering:
- text reads can show a summary like `Read 120 lines`
- image reads can show `Read image (42 KB)`
- PDF reads can show `Read PDF (1.2 MB)` or `Read 5 pages`
- `file_unchanged` can show `Unchanged since last read`

The full structured payload still exists in transcript storage for replay/translation, but the human UI can stay compact.
