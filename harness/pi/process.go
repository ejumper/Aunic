// Package pi manages a headless pi --mode rpc subprocess and provides a
// typed interface for sending RPC commands and receiving streamed events.
package pi

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

// Opts configures how the Pi subprocess is launched.
type Opts struct {
	// Binary is the pi executable name or absolute path. Defaults to "pi".
	Binary string
	// ModelID is Pi's --model flag value. Pi accepts a fully-qualified
	// "provider/id" string directly (e.g. "addie/local",
	// "openrouter/z-ai/glm-5.2"), so no separate --provider flag is needed.
	ModelID string
	// Tools controls which tools Pi exposes to the model.
	// nil → all tools (no --tools flag); empty slice → --no-tools; else --tools a,b,c
	Tools []string
	// SessionID is passed as --session-id. Pi creates or resumes a session file keyed to this ID.
	SessionID string
	// SessionDir is passed as --session-dir.
	SessionDir string
	// Cwd is the working directory for the subprocess (should be the note file's directory).
	Cwd string
	// SystemPrompt is passed as --system-prompt.
	SystemPrompt string
	// LogPath is where Pi's stderr is redirected. Defaults to ~/.local/share/aunic/logs/pi.log.
	LogPath string
	// SessionPath is an optional explicit session file path for --session (used for backup injection).
	// When set, this overrides the --session-id lookup.
	SessionPath string
}

// Process is a running pi --mode rpc subprocess.
type Process struct {
	cmd     *exec.Cmd
	stdin   *json.Encoder
	mu      sync.Mutex // guards stdin writes
	ch      chan []byte
	done    chan struct{}
	logFile *os.File
}

// Open spawns pi --mode rpc with the given options and returns a ready Process.
// The caller must call Close when done.
func Open(opts Opts) (*Process, error) {
	binary := opts.Binary
	if binary == "" {
		binary = "pi"
	}

	args := []string{"--mode", "rpc", "--no-context-files", "--no-skills"}

	if opts.ModelID != "" {
		args = append(args, "--model", opts.ModelID)
	}
	if opts.SystemPrompt != "" {
		args = append(args, "--system-prompt", opts.SystemPrompt)
	}

	// Session routing: explicit path takes priority over session-id.
	if opts.SessionPath != "" {
		args = append(args, "--session", opts.SessionPath)
	} else {
		if opts.SessionID != "" {
			args = append(args, "--session-id", opts.SessionID)
		}
		if opts.SessionDir != "" {
			args = append(args, "--session-dir", opts.SessionDir)
		}
	}

	// Tool control.
	switch {
	case opts.Tools == nil:
		// no flag — Pi uses all tools
	case len(opts.Tools) == 0:
		args = append(args, "--no-tools")
	default:
		args = append(args, "--tools", strings.Join(opts.Tools, ","))
	}

	cmd := exec.Command(binary, args...)
	if opts.Cwd != "" {
		cmd.Dir = opts.Cwd
	}

	stdinPipe, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("pi: stdin pipe: %w", err)
	}
	stdoutPipe, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("pi: stdout pipe: %w", err)
	}

	// Redirect stderr to log file.
	logPath := opts.LogPath
	if logPath == "" {
		logPath = defaultLogPath()
	}
	if err := os.MkdirAll(filepath.Dir(logPath), 0755); err != nil {
		return nil, fmt.Errorf("pi: log dir: %w", err)
	}
	logFile, err := os.OpenFile(logPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return nil, fmt.Errorf("pi: log file: %w", err)
	}
	cmd.Stderr = logFile

	if err := cmd.Start(); err != nil {
		logFile.Close()
		return nil, fmt.Errorf("pi: start: %w", err)
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
			_, _ = fmt.Fprintf(p.logFile, "pi stdout scanner: %v\n", err)
		}
		_ = cmd.Wait()
	}()

	return p, nil
}

// Output returns a channel that delivers raw JSON event lines from Pi's stdout.
// The channel is closed when the Pi process exits.
func (p *Process) Output() <-chan []byte {
	return p.ch
}

// SendPrompt sends a user prompt to the agent.
func (p *Process) SendPrompt(message string) error {
	return p.write(map[string]any{
		"type":    "prompt",
		"message": message,
	})
}

// SendBash executes a shell command inside Pi's context, injecting its output
// into the LLM context on the next prompt.
func (p *Process) SendBash(command string) error {
	return p.write(map[string]any{
		"type":    "bash",
		"command": command,
	})
}

// SendAbort requests the agent to abort its current operation.
func (p *Process) SendAbort() error {
	return p.write(map[string]any{"type": "abort"})
}

// SendFollowUp queues a follow-up message to be delivered after the agent finishes.
func (p *Process) SendFollowUp(message string) error {
	return p.write(map[string]any{
		"type":    "follow_up",
		"message": message,
	})
}

// GetState requests the current session state; the response arrives asynchronously on Output().
func (p *Process) GetState() error {
	return p.write(map[string]any{"type": "get_state"})
}

// SendUICancel responds to a dialog extension_ui_request with a cancellation,
// unblocking Pi's extension without requiring user interaction.
func (p *Process) SendUICancel(id string) error {
	return p.write(map[string]any{
		"type":      "extension_ui_response",
		"id":        id,
		"cancelled": true,
	})
}

// Close gracefully shuts down the Pi process.
func (p *Process) Close() error {
	// Signal the drain goroutine to stop accepting.
	select {
	case <-p.done:
	default:
		close(p.done)
	}
	// Best-effort abort so Pi exits cleanly.
	_ = p.write(map[string]any{"type": "abort"})
	if p.logFile != nil {
		p.logFile.Close()
	}
	return p.cmd.Process.Kill()
}

// write marshals v as a single JSON line to Pi's stdin.
func (p *Process) write(v any) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.stdin.Encode(v)
}

// defaultLogPath returns ~/.local/share/aunic/logs/pi.log.
func defaultLogPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".local", "share", "aunic", "logs", "pi.log")
}
