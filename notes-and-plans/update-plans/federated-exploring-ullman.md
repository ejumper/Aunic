# Plan: Context Window Usage Indicator

## Context

The user wants a live visual indicator of how full the LLM context window is. The chosen display location is the horizontal separator line inside the prompt editor box (the `─` line between the text input area and the model/mode control buttons). Characters from left to right are rendered in blue in proportion to how full the context window is. A `/context` slash command outputs the exact token counts to the indicator area.

---

## Files to Modify

| File | Purpose |
|------|---------|
| `src/aunic/tui/controller.py` | Context state tracking, fill fraction property, `/context` command |
| `src/aunic/tui/app.py` | Replace static separator `Window` with dynamic `_ContextSeparatorWindow` |
| `src/aunic/tui/rendering.py` | Add `context.separator` style class |
| `src/aunic/providers/llama_cpp.py` | Extract `model_context_window` from OpenAI-compatible response payload |

---

## Changes

### 1. Context state in `controller.py`

**In `__init__`**, add after `self._permission_future`:
```python
self._ctx_tokens_used: int | None = None       # input_tokens from last provider_response
self._ctx_window_size: int | None = None        # context window size (API or hardcoded)
self._ctx_last_file_len: int | None = None      # len(self._full_text) at last API call
```

**Add module-level lookup dict and helper** (near `_TOOL_VERBS`):
```python
_KNOWN_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus":    200_000,
    "claude-sonnet":  200_000,
    "claude-haiku":   200_000,
    "gpt-4o":         128_000,
    "gpt-4-turbo":    128_000,
    "o1":             200_000,
    "o3":             200_000,
}

def _known_context_window(model_option: ModelOption) -> int | None:
    name = model_option.model.lower()
    for prefix, size in _KNOWN_CONTEXT_WINDOWS.items():
        if prefix in name:
            return size
    return None
```

**Add two properties** on `TuiController`:
```python
@property
def context_fill_fraction(self) -> float | None:
    """Returns 0.0–1.0, or None if unknown."""
    used = self._effective_ctx_tokens()
    window = self._ctx_window_size or _known_context_window(self.state.selected_model)
    if used is None or window is None or window == 0:
        return None
    return min(1.0, used / window)

@property
def context_is_estimate(self) -> bool:
    if self._ctx_tokens_used is None or self._ctx_last_file_len is None:
        return False
    return len(self._full_text) != self._ctx_last_file_len

def _effective_ctx_tokens(self) -> int | None:
    if self._ctx_tokens_used is None:
        return None
    if self._ctx_last_file_len is None:
        return self._ctx_tokens_used
    delta = len(self._full_text) - self._ctx_last_file_len
    return max(0, self._ctx_tokens_used + delta // 4)
```

**Update `handle_progress_event`** — in the `provider_response` branch, after setting the verb status:
```python
elif loop_kind == "provider_response":
    usage = event.details.get("usage") or {}
    input_tokens = usage.get("input_tokens")
    model_context_window = usage.get("model_context_window")
    if input_tokens is not None:
        self._ctx_tokens_used = input_tokens
        self._ctx_last_file_len = len(self._full_text)
    if model_context_window is not None:
        self._ctx_window_size = model_context_window
    elif self._ctx_window_size is None:
        self._ctx_window_size = _known_context_window(self.state.selected_model)
    # existing verb display logic follows unchanged...
```

**Add `/context` interception in `send_prompt`** — insert before the generic `"/"` check (currently at line ~419):
```python
if stripped == "/context":
    self._handle_context_command()
    self._sync_prompt_text("")
    self._invalidate()
    return
```

**Add `_handle_context_command` method**:
```python
def _handle_context_command(self) -> None:
    used = self._effective_ctx_tokens()
    window = self._ctx_window_size or _known_context_window(self.state.selected_model)
    if used is None or window is None:
        self._set_status("Context window: unknown (run the model first)")
        return
    prefix = "~" if self.context_is_estimate else ""
    self._set_status(f"Context window: {prefix}{used:,}/{window:,}")
```

---

### 2. Extract `model_context_window` in `llama_cpp.py`

In `_usage_from_payload` (line ~466), add to the `Usage(...)` constructor:
```python
model_context_window=(
    _coerce_int(usage.get("model_context_window"))
    or _coerce_int(usage.get("context_window"))
    or _coerce_int(usage.get("context_length"))
),
```
These cover OpenRouter and other providers that may include a non-standard context window field.

---

### 3. Add style in `rendering.py`

In `build_tui_style()`, add one entry:
```python
"context.separator": "ansiblue",
```

---

### 4. Dynamic separator in `app.py`

**Replace** the static `Window(height=1, char="─")` inside `prompt_box` (line 194):
```python
# before:
Window(height=1, char="─"),
# after:
_ContextSeparatorWindow(self.controller),
```

**Add** the `_ContextSeparatorWindow` class (at module level, near `ModelPickerView`):
```python
class _ContextSeparatorWindow(Window):
    """Separator that fills left-to-right in blue proportional to context window fill."""

    def __init__(self, controller: TuiController) -> None:
        self._controller = controller
        self._width = 1
        super().__init__(
            FormattedTextControl(text=self._render, focusable=False, show_cursor=False),
            height=1,
        )

    def _render(self) -> StyleAndTextTuples:
        w = max(1, self._width)
        fill = self._controller.context_fill_fraction
        if fill is None:
            return [("", "─" * w)]
        filled = max(0, min(w, round(fill * w)))
        fragments: StyleAndTextTuples = []
        if filled > 0:
            fragments.append(("class:context.separator", "─" * filled))
        if filled < w:
            fragments.append(("", "─" * (w - filled)))
        return fragments

    def write_to_screen(self, screen, mouse_handlers, write_position,
                        parent_style, erase_bg, z_index) -> None:
        self._width = write_position.width
        super().write_to_screen(screen, mouse_handlers, write_position,
                                parent_style, erase_bg, z_index)
```

**Add imports** at top of `app.py` if not already present:
- `StyleAndTextTuples` from `prompt_toolkit.formatted_text`

---

## Behaviour Summary

| Situation | Separator | `/context` output |
|---|---|---|
| No run yet | All `─` (no colour) | "Context window: unknown (run the model first)" |
| After a run, file unchanged | Fraction blue, fraction plain | "Context window: 12,450/200,000" |
| After a run, file edited | Fraction blue (estimated) | "Context window: ~13,200/200,000" |
| Context window unknown (anon OpenRouter model) | No colour until API returns it | Same |

The fill fraction updates after each `provider_response` event during a run (so it refreshes turn-by-turn in a multi-turn run). Between runs, the estimate drifts by ±1 token per 4 characters changed.

---

## Verification

1. `(.venv) python -c "from aunic.tui.app import TuiApp; print('OK')"` — check imports
2. Start the app; confirm separator is plain `─` with no colour before any run
3. Run the model; watch separator gradually fill in blue as turns complete
4. Edit some text after the run; confirm fill adjusts slightly (estimate)
5. Type `/context` and hit Ctrl+R; confirm indicator shows token counts with `~` prefix if file changed
6. Run again; confirm `/context` shows exact counts with no `~`
7. With a Codex model: confirm `model_context_window` is populated from the API (non-None)
8. With a Claude model: confirm fill works via hardcoded 200,000 limit
