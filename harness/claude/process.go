// Package claude manages a headless claude subprocess (--print,
// --output-format/--input-format stream-json) and provides a typed interface
// for sending prompts and receiving streamed events.
package claude

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
)

// Opts configures how the Claude subprocess is launched.
type Opts struct {
	// Binary is the claude executable name or absolute path. Defaults to "claude".
	Binary string
	// ModelID is Claude's --model flag value (an alias like "sonnet"/"opus", or
	// a full model name).
	ModelID string
	// Tools controls which tools Claude exposes to the model.
	// nil → no --tools flag (Claude's own default set); empty slice → --tools ""; else --tools a,b,c
	Tools []string
	// SessionID is passed as --session-id (must be a valid UUID string) or,
	// when Resume is true, as --resume instead.
	SessionID string
	// Resume selects --resume <SessionID> instead of --session-id <SessionID>.
	Resume bool
	// Cwd is the working directory for the subprocess (should be the note file's directory).
	Cwd string
	// SystemPrompt is passed as --system-prompt. Should be static text (no
	// per-note substitution) so Anthropic's prompt cache can share a prefix
	// across every note/session — see harness/claude doc comments on cost.
	SystemPrompt string
	// LogPath is where Claude's stderr is redirected. Defaults to ~/.local/share/aunic/logs/claude.log.
	LogPath string
}

// Process is a running claude --print --output-format stream-json subprocess.
type Process struct {
	cmd     *exec.Cmd
	stdin   *json.Encoder
	mu      sync.Mutex // guards stdin writes
	ch      chan []byte
	done    chan struct{}
	logFile *os.File
}

// Open spawns claude with the given options and returns a ready Process.
// The caller must call Close when done.
//
// Fixed launch args are always present, confirmed necessary via live testing:
//   - --print --output-format stream-json --input-format stream-json
//     --include-partial-messages --verbose: the headless streaming protocol.
//   - --permission-mode bypassPermissions: without it, any tool call needing
//     approval hangs indefinitely (no auto-deny, no timeout) since nothing
//     answers the underlying control_request.
//   - --strict-mcp-config (with no --mcp-config passed, so it resolves to zero
//     MCP servers) + --safe-mode: without these, the subprocess silently loads
//     the user's entire personal environment (connected MCP servers, skills,
//     agents, slash commands) on every session — confirmed live to cost ~11x
//     more and to cause a real cache-invalidating bug where the tool list
//     drifts between turns ("tools_changed" cache miss). --bare was
//     considered and rejected: it forces ANTHROPIC_API_KEY/apiKeyHelper-only
//     auth and never reads OAuth/keychain, which would silently switch a
//     subscription-authenticated account onto metered API billing.
func Open(opts Opts) (*Process, error) {
	binary := opts.Binary
	if binary == "" {
		binary = "claude"
	}

	args := []string{
		"--print",
		"--output-format", "stream-json",
		"--input-format", "stream-json",
		"--include-partial-messages",
		"--verbose",
		"--permission-mode", "bypassPermissions",
		"--strict-mcp-config",
		"--safe-mode",
	}

	if opts.ModelID != "" {
		args = append(args, "--model", opts.ModelID)
	}
	if opts.SystemPrompt != "" {
		args = append(args, "--system-prompt", opts.SystemPrompt)
	}

	if opts.SessionID != "" {
		if opts.Resume {
			args = append(args, "--resume", opts.SessionID)
		} else {
			args = append(args, "--session-id", opts.SessionID)
		}
	}

	// Tool control.
	switch {
	case opts.Tools == nil:
		// no flag — Claude uses its own default built-in tool set
	case len(opts.Tools) == 0:
		args = append(args, "--tools", "")
	default:
		args = append(args, "--tools", strings.Join(opts.Tools, ","))
	}

	cmd := exec.Command(binary, args...)
	if opts.Cwd != "" {
		cmd.Dir = opts.Cwd
	}

	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("claude: stdin pipe: %w", err)
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("claude: stdout pipe: %w", err)
	}

	// Redirect stderr to log file.
	logPath := opts.LogPath
	if logPath == "" {
		logPath = defaultLogPath()
	}
	if err := os.MkdirAll(filepath.Dir(logPath), 0755); err != nil {
		return nil, fmt.Errorf("claude: log dir: %w", err)
	}
	logFile, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return nil, fmt.Errorf("claude: log file: %w", err)
	}
	cmd.Stderr = logFile

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return nil, fmt.Errorf("claude: start: %w", err)
	}

	p := &Process{
		cmd:     cmd,
		stdin:   json.NewEncoder(stdinPipe),
		ch:      make(chan []byte, 32),
		done:    make(chan struct{}),
		logFile: logFile,
	}

	// Drain stdout into channel.
	go func() {
		defer close(p.ch)
		scanner := bufio.NewScanner(stdoutPipe)
		scanner.Buffer(make([]byte, 1024*1024), 1024*1024) // 1 MiB line buffer
		for scanner.Scan() {
			line := scanner.Bytes()
			if len(line) == 0 {
				continue
			}
			cp := make([]byte, len(line))
			copy(cp, line)
			select {
			case p.ch <- cp:
			case <-p.done:
				return
			}
		}
		// Scanner finished (EOF or error). Log scan errors but don't surface them —
		// the closed channel signals callers that the process is gone.
		if err := scanner.Err(); err != nil {
			_, _ = fmt.Fprintf(p.logFile, "claude stdout scanner: %v\n", err)
		}
		_ = cmd.Wait()
	}()

	return p, nil
}

// Output returns a channel that delivers raw JSON event lines from Claude's
// stdout. The channel is closed when the process exits.
func (p *Process) Output() <-chan []byte {
	return p.ch
}

// SendPrompt sends a user prompt to the agent as a stream-json input line.
func (p *Process) SendPrompt(message string) error {
	return p.write(map[string]any{
		"type": "user",
		"message": map[string]any{
			"role": "user",
			"content": []map[string]any{
				{"type": "text", "text": message},
			},
		},
	})
}

// Close shuts down the Claude process.
func (p *Process) Close() error {
	// Signal the drain goroutine to stop accepting.
	select {
	case <-p.done:
	default:
		close(p.done)
	}
	if p.logFile != nil {
		p.logFile.Close()
	}
	return p.cmd.Process.Kill()
}

// write marshals v as a single JSON line to Claude's stdin.
func (p *Process) write(v any) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.stdin.Encode(v)
}

// defaultLogPath returns ~/.local/share/aunic/logs/claude.log.
func defaultLogPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "share", "aunic", "logs", "claude.log")
}
