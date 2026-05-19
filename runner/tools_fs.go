package runner

import (
	"bufio"
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

//go:embed desc_read.md
var readToolDesc string

//go:embed desc_write.md
var writeToolDesc string

//go:embed desc_edit.md
var editToolDesc string

//go:embed desc_grep.md
var grepToolDesc string

//go:embed desc_glob.md
var globToolDesc string

// ─── Read ─────────────────────────────────────────────────────────────────────

const readMaxLines = 2000

// blockedDevicePaths are paths that would hang or produce infinite output.
var blockedDevicePaths = map[string]bool{
	"/dev/zero": true, "/dev/random": true, "/dev/urandom": true, "/dev/full": true,
	"/dev/stdin": true, "/dev/tty": true, "/dev/console": true,
	"/dev/stdout": true, "/dev/stderr": true,
	"/dev/fd/0": true, "/dev/fd/1": true, "/dev/fd/2": true,
}

func isBlockedDevice(path string) bool {
	if blockedDevicePaths[path] {
		return true
	}
	// /proc/<pid>/fd/0-2 are Linux aliases for stdio
	return strings.HasPrefix(path, "/proc/") &&
		(strings.HasSuffix(path, "/fd/0") ||
			strings.HasSuffix(path, "/fd/1") ||
			strings.HasSuffix(path, "/fd/2"))
}

type readArgs struct {
	FilePath string `json:"file_path"`
	Offset   int    `json:"offset,omitempty"` // 1-based line number to start from
	Limit    int    `json:"limit,omitempty"`
}

type readTool struct{}

func (readTool) Name() string        { return "Read" }
func (readTool) Description() string { return readToolDesc }
func (readTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"file_path": map[string]any{"type": "string", "description": "Absolute path to the file to read."},
			"offset":    map[string]any{"type": "integer", "description": "Line number to start reading from (1-based). Defaults to 1."},
			"limit":     map[string]any{"type": "integer", "description": "Maximum number of lines to read."},
		},
		"required":             []string{"file_path"},
		"additionalProperties": false,
	}
}

func (readTool) Execute(_ context.Context, _ *RunContext, argsJSON string) Result {
	var args readArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.FilePath == "" {
		return errorResult("empty_path", "file_path must not be empty.")
	}
	if !filepath.IsAbs(args.FilePath) {
		return errorResult("relative_path", "file_path must be an absolute path, not a relative path.")
	}
	if isBlockedDevice(args.FilePath) {
		return errorResult("blocked_path", fmt.Sprintf("reading %s is not allowed.", args.FilePath))
	}

	data, err := os.ReadFile(args.FilePath)
	if err != nil {
		if os.IsNotExist(err) {
			return errorResult("file_not_found", fmt.Sprintf("File not found: %s", args.FilePath))
		}
		return errorResult("read_failed", err.Error())
	}

	if len(data) == 0 {
		b, _ := json.Marshal(map[string]any{"content": "", "lines_read": 0, "empty": true})
		return Result{JSON: string(b), Summary: "empty file"}
	}

	lines := strings.Split(string(data), "\n")
	// Drop the trailing empty element from a trailing newline
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	totalLines := len(lines)

	// Apply offset (1-based → 0-based index)
	start := 0
	if args.Offset > 1 {
		start = args.Offset - 1
	}
	if start >= len(lines) {
		b, _ := json.Marshal(map[string]any{"content": "", "lines_read": 0, "total_lines": totalLines})
		return Result{JSON: string(b), Summary: "0 lines (offset past end)"}
	}
	lines = lines[start:]

	// Apply limit
	limit := readMaxLines
	if args.Limit > 0 {
		limit = args.Limit
	}
	truncated := false
	if len(lines) > limit {
		lines = lines[:limit]
		truncated = true
	}

	// Format cat -n style: "     1\tline content\n"
	var sb strings.Builder
	for i, line := range lines {
		fmt.Fprintf(&sb, "%6d\t%s\n", start+i+1, line)
	}

	linesRead := len(lines)
	b, _ := json.Marshal(map[string]any{
		"content":     sb.String(),
		"lines_read":  linesRead,
		"total_lines": totalLines,
		"truncated":   truncated,
	})
	summary := fmt.Sprintf("%s (%d lines)", filepath.Base(args.FilePath), linesRead)
	if truncated {
		summary += " [truncated]"
	}
	return Result{JSON: string(b), Summary: summary}
}

// ─── Write ────────────────────────────────────────────────────────────────────

type writeArgs struct {
	FilePath string `json:"file_path"`
	Content  string `json:"content"`
}

type writeTool struct{}

func (writeTool) Name() string        { return "Write" }
func (writeTool) Description() string { return writeToolDesc }
func (writeTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"file_path": map[string]any{"type": "string", "description": "The absolute path to the file to write (must be absolute, not relative)."},
			"content":   map[string]any{"type": "string", "description": "The content to write to the file."},
		},
		"required":             []string{"file_path", "content"},
		"additionalProperties": false,
	}
}

func (writeTool) Execute(_ context.Context, _ *RunContext, argsJSON string) Result {
	var args writeArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.FilePath == "" {
		return errorResult("empty_path", "file_path must not be empty.")
	}
	if !filepath.IsAbs(args.FilePath) {
		return errorResult("relative_path", "file_path must be an absolute path.")
	}
	if err := os.MkdirAll(filepath.Dir(args.FilePath), 0o755); err != nil {
		return errorResult("mkdir_failed", err.Error())
	}
	if err := os.WriteFile(args.FilePath, []byte(args.Content), 0o644); err != nil {
		return errorResult("write_failed", err.Error())
	}
	b, _ := json.Marshal(map[string]any{"file_path": args.FilePath, "bytes": len(args.Content)})
	return Result{JSON: string(b), Summary: fmt.Sprintf("wrote %d bytes", len(args.Content))}
}

// ─── Edit ─────────────────────────────────────────────────────────────────────

type editArgs struct {
	FilePath   string `json:"file_path"`
	OldString  string `json:"old_string"`
	NewString  string `json:"new_string"`
	ReplaceAll bool   `json:"replace_all,omitempty"`
}

type editTool struct{}

func (editTool) Name() string        { return "Edit" }
func (editTool) Description() string { return editToolDesc }
func (editTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"file_path":   map[string]any{"type": "string", "description": "The absolute path to the file to modify."},
			"old_string":  map[string]any{"type": "string", "description": "The text to replace. Must match exactly as it appears in the file."},
			"new_string":  map[string]any{"type": "string", "description": "The text to replace it with (must be different from old_string)."},
			"replace_all": map[string]any{"type": "boolean", "description": "Replace all occurrences of old_string (default false)."},
		},
		"required":             []string{"file_path", "old_string", "new_string"},
		"additionalProperties": false,
	}
}

func (editTool) Execute(_ context.Context, _ *RunContext, argsJSON string) Result {
	var args editArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.FilePath == "" {
		return errorResult("empty_path", "file_path must not be empty.")
	}
	if !filepath.IsAbs(args.FilePath) {
		return errorResult("relative_path", "file_path must be an absolute path.")
	}
	if args.OldString == "" {
		return errorResult("empty_old_string", "old_string must not be empty.")
	}
	if args.OldString == args.NewString {
		return errorResult("no_op", "old_string equals new_string; nothing to change.")
	}

	data, err := os.ReadFile(args.FilePath)
	if err != nil {
		if os.IsNotExist(err) {
			return errorResult("file_not_found", fmt.Sprintf("File not found: %s", args.FilePath))
		}
		return errorResult("read_failed", err.Error())
	}
	content := string(data)

	actual := findActualString(content, args.OldString)
	if actual == "" {
		return errorResult("old_string_not_found",
			"old_string not found in the file. Make sure it matches the file content exactly, including whitespace and indentation.")
	}

	if !args.ReplaceAll {
		count := strings.Count(content, actual)
		if count > 1 {
			return errorResult("multiple_matches",
				fmt.Sprintf("old_string occurs %d times; set replace_all=true or use a more specific string.", count))
		}
	}

	newStr := preserveQuoteStyle(args.OldString, actual, args.NewString)
	updated := applyEditToFile(content, actual, newStr, args.ReplaceAll)
	if updated == content {
		return errorResult("no_change", "Edit produced no change in the file.")
	}

	if err := os.WriteFile(args.FilePath, []byte(updated), 0o644); err != nil {
		return errorResult("write_failed", err.Error())
	}

	replacements := 1
	if args.ReplaceAll {
		replacements = strings.Count(content, actual)
	}
	b, _ := json.Marshal(map[string]any{"file_path": args.FilePath, "replacements": replacements})
	summary := fmt.Sprintf("edited %s", filepath.Base(args.FilePath))
	if replacements > 1 {
		summary = fmt.Sprintf("edited %s (%d replacements)", filepath.Base(args.FilePath), replacements)
	}
	return Result{JSON: string(b), Summary: summary}
}

// ── Edit helpers (ported from claude-code FileEditTool/utils.ts) ──────────────

const (
	leftSingleQuote  = "‘" // '
	rightSingleQuote = "’" // '
	leftDoubleQuote  = "“" // "
	rightDoubleQuote = "”" // "
)

// normalizeQuotes replaces typographic (curly) quotes with ASCII straight quotes.
func normalizeQuotes(s string) string {
	s = strings.ReplaceAll(s, leftSingleQuote, "'")
	s = strings.ReplaceAll(s, rightSingleQuote, "'")
	s = strings.ReplaceAll(s, leftDoubleQuote, "\"")
	s = strings.ReplaceAll(s, rightDoubleQuote, "\"")
	return s
}

// findActualString finds searchString in fileContent via exact match first,
// then quote-normalized match. Returns the actual substring from the file, or "".
func findActualString(fileContent, searchString string) string {
	if strings.Contains(fileContent, searchString) {
		return searchString
	}
	normSearch := normalizeQuotes(searchString)
	normFile := normalizeQuotes(fileContent)
	idx := strings.Index(normFile, normSearch)
	if idx == -1 {
		return ""
	}
	// normalizeQuotes preserves rune count (each curly quote → one ASCII quote),
	// so we can map from rune position in normFile to byte position in fileContent.
	runesBefore := utf8.RuneCountInString(normFile[:idx])
	runesInMatch := utf8.RuneCountInString(normSearch)

	startByte, endByte := -1, len(fileContent)
	runeIdx := 0
	for byteIdx := range fileContent {
		if runeIdx == runesBefore {
			startByte = byteIdx
		}
		if runeIdx == runesBefore+runesInMatch {
			endByte = byteIdx
			break
		}
		runeIdx++
	}
	if startByte == -1 {
		return ""
	}
	return fileContent[startByte:endByte]
}

// preserveQuoteStyle applies the curly-quote style from actualOldString to newString
// when the match was found via quote normalization.
func preserveQuoteStyle(oldString, actualOldString, newString string) string {
	if oldString == actualOldString {
		return newString
	}
	hasDouble := strings.ContainsAny(actualOldString, leftDoubleQuote+rightDoubleQuote)
	hasSingle := strings.ContainsAny(actualOldString, leftSingleQuote+rightSingleQuote)
	if !hasDouble && !hasSingle {
		return newString
	}
	result := newString
	if hasDouble {
		result = applyCurlyDoubleQuotes(result)
	}
	if hasSingle {
		result = applyCurlySingleQuotes(result)
	}
	return result
}

func isOpeningQuoteContext(prev rune) bool {
	return prev == 0 || prev == ' ' || prev == '\t' || prev == '\n' || prev == '\r' ||
		prev == '(' || prev == '[' || prev == '{' ||
		prev == '—' || prev == '–' // em dash, en dash
}

func applyCurlyDoubleQuotes(s string) string {
	runes := []rune(s)
	var b strings.Builder
	for i, r := range runes {
		if r == '"' {
			var prev rune
			if i > 0 {
				prev = runes[i-1]
			}
			if isOpeningQuoteContext(prev) {
				b.WriteString(leftDoubleQuote)
			} else {
				b.WriteString(rightDoubleQuote)
			}
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

func applyCurlySingleQuotes(s string) string {
	runes := []rune(s)
	var b strings.Builder
	for i, r := range runes {
		if r == '\'' {
			var prev, next rune
			if i > 0 {
				prev = runes[i-1]
			}
			if i < len(runes)-1 {
				next = runes[i+1]
			}
			// Apostrophe between two letters = contraction → right single quote
			if unicode.IsLetter(prev) && unicode.IsLetter(next) {
				b.WriteString(rightSingleQuote)
			} else {
				var p rune
				if i > 0 {
					p = runes[i-1]
				}
				if isOpeningQuoteContext(p) {
					b.WriteString(leftSingleQuote)
				} else {
					b.WriteString(rightSingleQuote)
				}
			}
		} else {
			b.WriteRune(r)
		}
	}
	return b.String()
}

// applyEditToFile applies the old→new replacement. When newStr is empty (deletion),
// it strips the trailing newline if old_string+"\n" is in the content.
func applyEditToFile(content, oldStr, newStr string, replaceAll bool) string {
	if newStr != "" {
		if replaceAll {
			return strings.ReplaceAll(content, oldStr, newStr)
		}
		return strings.Replace(content, oldStr, newStr, 1)
	}
	// Deletion: strip trailing newline for clean line removal
	search := oldStr
	if !strings.HasSuffix(oldStr, "\n") && strings.Contains(content, oldStr+"\n") {
		search = oldStr + "\n"
	}
	if replaceAll {
		return strings.ReplaceAll(content, search, "")
	}
	return strings.Replace(content, search, "", 1)
}

// ─── Grep ─────────────────────────────────────────────────────────────────────

const grepDefaultHeadLimit = 250

type grepArgs struct {
	Pattern         string `json:"pattern"`
	Path            string `json:"path,omitempty"`
	Glob            string `json:"glob,omitempty"`
	OutputMode      string `json:"output_mode,omitempty"`
	ContextBefore   int    `json:"-B,omitempty"`
	ContextAfter    int    `json:"-A,omitempty"`
	ContextC        int    `json:"-C,omitempty"`
	Context         int    `json:"context,omitempty"`
	ShowLineNumbers *bool  `json:"-n,omitempty"`
	CaseInsensitive bool   `json:"-i,omitempty"`
	Type            string `json:"type,omitempty"`
	HeadLimit       *int   `json:"head_limit,omitempty"`
	Offset          int    `json:"offset,omitempty"`
	Multiline       bool   `json:"multiline,omitempty"`
}

var grepVCSExclusions = []string{".git", ".svn", ".hg", ".bzr", ".jj", ".sl"}

type grepTool struct{}

func (grepTool) Name() string        { return "Grep" }
func (grepTool) Description() string { return grepToolDesc }
func (grepTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"pattern":      map[string]any{"type": "string", "description": "The regular expression pattern to search for in file contents."},
			"path":         map[string]any{"type": "string", "description": "File or directory to search in. Defaults to the directory of the active note."},
			"glob":         map[string]any{"type": "string", "description": `Glob pattern to filter files (e.g. "*.go", "*.{ts,tsx}").`},
			"output_mode":  map[string]any{"type": "string", "enum": []string{"content", "files_with_matches", "count"}, "description": `Output mode. Defaults to "files_with_matches".`},
			"-B":           map[string]any{"type": "integer", "description": "Lines to show before each match (content mode only)."},
			"-A":           map[string]any{"type": "integer", "description": "Lines to show after each match (content mode only)."},
			"-C":           map[string]any{"type": "integer", "description": "Lines to show before and after each match (content mode only)."},
			"context":      map[string]any{"type": "integer", "description": "Alias for -C."},
			"-n":           map[string]any{"type": "boolean", "description": "Show line numbers (content mode, default true)."},
			"-i":           map[string]any{"type": "boolean", "description": "Case-insensitive search."},
			"type":         map[string]any{"type": "string", "description": `File type filter (e.g. "go", "py", "js").`},
			"head_limit":   map[string]any{"type": "integer", "description": "Limit output to first N lines/entries. Defaults to 250. Pass 0 for unlimited."},
			"offset":       map[string]any{"type": "integer", "description": "Skip first N results before applying head_limit."},
			"multiline":    map[string]any{"type": "boolean", "description": "Enable multiline matching (. matches newlines). Default false."},
		},
		"required":             []string{"pattern"},
		"additionalProperties": false,
	}
}

func (grepTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args grepArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.Pattern == "" {
		return errorResult("empty_pattern", "pattern must not be empty.")
	}

	searchPath := filepath.Dir(rc.ActivePath)
	if args.Path != "" {
		if !filepath.IsAbs(args.Path) {
			return errorResult("relative_path", "path must be an absolute path.")
		}
		searchPath = args.Path
	}

	mode := args.OutputMode
	if mode == "" {
		mode = "files_with_matches"
	}

	rgArgs := buildGrepArgs(args, mode, searchPath)
	cmd := exec.CommandContext(ctx, "rg", rgArgs...)
	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			switch exitErr.ExitCode() {
			case 1:
				out = nil // no matches — not an error
			default:
				if errors.Is(err, exec.ErrNotFound) {
					return errorResult("rg_not_found", "ripgrep (rg) is required but not installed.")
				}
				stderr := strings.TrimSpace(string(exitErr.Stderr))
				return errorResult("search_failed", fmt.Sprintf("ripgrep: %s", stderr))
			}
		} else if errors.Is(err, exec.ErrNotFound) {
			return errorResult("rg_not_found", "ripgrep (rg) is required but not installed.")
		} else {
			return errorResult("search_failed", err.Error())
		}
	}

	var lines []string
	if len(out) > 0 {
		scanner := bufio.NewScanner(bytes.NewReader(out))
		for scanner.Scan() {
			lines = append(lines, scanner.Text())
		}
	}

	// Determine effective head limit
	effectiveLimit := grepDefaultHeadLimit
	if args.HeadLimit != nil {
		effectiveLimit = *args.HeadLimit // 0 = unlimited
	}

	// Apply offset then limit
	if args.Offset > 0 {
		if args.Offset >= len(lines) {
			lines = nil
		} else {
			lines = lines[args.Offset:]
		}
	}
	var appliedLimit *int
	if effectiveLimit > 0 && len(lines) > effectiveLimit {
		lines = lines[:effectiveLimit]
		appliedLimit = &effectiveLimit
	}

	// Relativize paths in output
	relLines := make([]string, len(lines))
	for i, line := range lines {
		relLines[i] = relativizeLine(line, searchPath)
	}

	result := map[string]any{}
	switch mode {
	case "content":
		result["mode"] = "content"
		result["content"] = strings.Join(relLines, "\n")
		result["num_files"] = 0
		result["filenames"] = []string{}
		result["num_lines"] = len(relLines)
	case "count":
		var totalMatches, fileCount int
		for _, line := range relLines {
			if idx := strings.LastIndex(line, ":"); idx > 0 {
				if n, err := strconv.Atoi(line[idx+1:]); err == nil {
					totalMatches += n
					fileCount++
				}
			}
		}
		result["mode"] = "count"
		result["content"] = strings.Join(relLines, "\n")
		result["num_files"] = fileCount
		result["filenames"] = []string{}
		result["num_matches"] = totalMatches
	default: // files_with_matches
		result["mode"] = "files_with_matches"
		result["filenames"] = relLines
		result["num_files"] = len(relLines)
	}
	if appliedLimit != nil {
		result["applied_limit"] = *appliedLimit
	}
	if args.Offset > 0 {
		result["applied_offset"] = args.Offset
	}

	b, _ := json.Marshal(result)
	nFiles := len(relLines)
	var summary string
	switch mode {
	case "content":
		summary = fmt.Sprintf("%d lines matched %q", nFiles, args.Pattern)
	case "count":
		summary = fmt.Sprintf("count: %q", args.Pattern)
	default:
		summary = fmt.Sprintf("%d files matched %q", nFiles, args.Pattern)
	}
	return Result{JSON: string(b), Summary: summary}
}

func buildGrepArgs(args grepArgs, mode, searchPath string) []string {
	a := []string{"--hidden"}
	for _, dir := range grepVCSExclusions {
		a = append(a, "--glob", "!"+dir)
	}
	a = append(a, "--max-columns", "500")

	if args.Multiline {
		a = append(a, "-U", "--multiline-dotall")
	}
	if args.CaseInsensitive {
		a = append(a, "-i")
	}

	switch mode {
	case "files_with_matches":
		a = append(a, "-l")
	case "count":
		a = append(a, "-c")
	case "content":
		showLineNums := true
		if args.ShowLineNumbers != nil {
			showLineNums = *args.ShowLineNumbers
		}
		if showLineNums {
			a = append(a, "-n")
		}
		if args.Context != 0 {
			a = append(a, "-C", strconv.Itoa(args.Context))
		} else if args.ContextC != 0 {
			a = append(a, "-C", strconv.Itoa(args.ContextC))
		} else {
			if args.ContextBefore != 0 {
				a = append(a, "-B", strconv.Itoa(args.ContextBefore))
			}
			if args.ContextAfter != 0 {
				a = append(a, "-A", strconv.Itoa(args.ContextAfter))
			}
		}
	}

	if strings.HasPrefix(args.Pattern, "-") {
		a = append(a, "-e", args.Pattern)
	} else {
		a = append(a, args.Pattern)
	}

	if args.Type != "" {
		a = append(a, "--type", args.Type)
	}
	for _, g := range parseGlobPatterns(args.Glob) {
		a = append(a, "--glob", g)
	}

	a = append(a, searchPath)
	return a
}

func parseGlobPatterns(glob string) []string {
	if glob == "" {
		return nil
	}
	var out []string
	for _, raw := range strings.Fields(glob) {
		if strings.ContainsAny(raw, "{}") {
			out = append(out, raw)
		} else {
			for _, g := range strings.Split(raw, ",") {
				if g != "" {
					out = append(out, g)
				}
			}
		}
	}
	return out
}

// relativizeLine converts absolute paths in a grep output line to relative.
// Handles both bare file paths (files_with_matches) and path:...:content lines.
func relativizeLine(line, base string) string {
	if !filepath.IsAbs(line) {
		return line
	}
	// Try bare path (files_with_matches / count format)
	if rel, err := filepath.Rel(base, line); err == nil {
		return rel
	}
	// Try path before first colon (content format: /abs/path:linenum:content)
	if idx := strings.Index(line, ":"); idx > 0 {
		p := line[:idx]
		if filepath.IsAbs(p) {
			if rel, err := filepath.Rel(base, p); err == nil {
				return rel + line[idx:]
			}
		}
	}
	return line
}

// ─── Glob ─────────────────────────────────────────────────────────────────────

const globMaxResults = 100

type globArgs struct {
	Pattern string `json:"pattern"`
	Path    string `json:"path,omitempty"`
}

type globTool struct{}

func (globTool) Name() string        { return "Glob" }
func (globTool) Description() string { return globToolDesc }
func (globTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"pattern": map[string]any{"type": "string", "description": "The glob pattern to match files against (e.g. \"**/*.go\", \"src/**/*.ts\")."},
			"path":    map[string]any{"type": "string", "description": "Directory to search in. Defaults to the directory of the active note. Must be an absolute path if provided."},
		},
		"required":             []string{"pattern"},
		"additionalProperties": false,
	}
}

func (globTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args globArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.Pattern == "" {
		return errorResult("empty_pattern", "pattern must not be empty.")
	}

	searchPath := filepath.Dir(rc.ActivePath)
	if args.Path != "" {
		if !filepath.IsAbs(args.Path) {
			return errorResult("relative_path", "path must be an absolute path.")
		}
		info, err := os.Stat(args.Path)
		if err != nil {
			if os.IsNotExist(err) {
				return errorResult("path_not_found", fmt.Sprintf("directory not found: %s", args.Path))
			}
			return errorResult("stat_failed", err.Error())
		}
		if !info.IsDir() {
			return errorResult("not_a_directory", fmt.Sprintf("path is not a directory: %s", args.Path))
		}
		searchPath = args.Path
	}

	rgArgs := []string{"--files", "--hidden", "--glob", args.Pattern, searchPath}
	cmd := exec.CommandContext(ctx, "rg", rgArgs...)
	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			if exitErr.ExitCode() == 1 {
				out = nil // no matches
			} else if errors.Is(err, exec.ErrNotFound) {
				return errorResult("rg_not_found", "ripgrep (rg) is required but not installed.")
			} else {
				return errorResult("glob_failed", fmt.Sprintf("ripgrep: %s", strings.TrimSpace(string(exitErr.Stderr))))
			}
		} else if errors.Is(err, exec.ErrNotFound) {
			return errorResult("rg_not_found", "ripgrep (rg) is required but not installed.")
		} else {
			return errorResult("glob_failed", err.Error())
		}
	}

	var files []string
	if len(out) > 0 {
		for _, line := range strings.Split(strings.TrimRight(string(out), "\n"), "\n") {
			if line != "" {
				files = append(files, line)
			}
		}
	}

	// Sort by modification time descending, then by path as tiebreaker
	type fileEntry struct {
		path  string
		mtime int64
	}
	entries := make([]fileEntry, 0, len(files))
	for _, f := range files {
		info, err := os.Stat(f)
		var mtime int64
		if err == nil {
			mtime = info.ModTime().UnixMilli()
		}
		entries = append(entries, fileEntry{f, mtime})
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].mtime != entries[j].mtime {
			return entries[i].mtime > entries[j].mtime
		}
		return entries[i].path < entries[j].path
	})

	truncated := false
	if len(entries) > globMaxResults {
		entries = entries[:globMaxResults]
		truncated = true
	}

	filenames := make([]string, len(entries))
	for i, e := range entries {
		if rel, err := filepath.Rel(searchPath, e.path); err == nil {
			filenames[i] = rel
		} else {
			filenames[i] = e.path
		}
	}

	b, _ := json.Marshal(map[string]any{
		"filenames": filenames,
		"num_files": len(filenames),
		"truncated": truncated,
	})
	summary := fmt.Sprintf("%d files matched %q", len(filenames), args.Pattern)
	if truncated {
		summary += " [truncated at 100]"
	}
	return Result{JSON: string(b), Summary: summary}
}
