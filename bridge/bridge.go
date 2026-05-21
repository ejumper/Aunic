package bridge

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

// Process wraps the node subprocess running the TS bridge. Spawn one per run.
type Process struct {
	cmd      *exec.Cmd
	stdin    io.WriteCloser
	stdout   *bufio.Reader
	stderr   *bufio.Reader
	mu       sync.Mutex // serializes writes to stdin
	closed   bool
	closeMu  sync.Mutex
	doneOnce sync.Once
	done     chan struct{}
}

// Spawn launches the bridge subprocess and returns a Process ready for
// SendStart. The caller must call Close() when done.
//
// bridgeDir is the directory containing dist/bridge.js and node_modules.
func Spawn(ctx context.Context, bridgeDir string) (*Process, error) {
	bridgeJS := filepath.Join(bridgeDir, "dist", "bridge.js")
	if _, err := os.Stat(bridgeJS); err != nil {
		return nil, fmt.Errorf("bridge: missing %s — run `npm install && npm run build` in %s",
			bridgeJS, bridgeDir)
	}
	nodeModules := filepath.Join(bridgeDir, "node_modules", "@anthropic-ai", "claude-agent-sdk")
	if _, err := os.Stat(nodeModules); err != nil {
		return nil, fmt.Errorf("bridge: missing %s — run `npm install` in %s",
			nodeModules, bridgeDir)
	}

	cmd := exec.CommandContext(ctx, "node", bridgeJS)
	cmd.Dir = bridgeDir
	cmd.Env = os.Environ()
	// Allow grace-period shutdown on cancel rather than hard kill.
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	cmd.WaitDelay = 5 * time.Second

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, fmt.Errorf("bridge: stdin pipe: %w", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, fmt.Errorf("bridge: stdout pipe: %w", err)
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, fmt.Errorf("bridge: stderr pipe: %w", err)
	}

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("bridge: start node: %w", err)
	}

	p := &Process{
		cmd:    cmd,
		stdin:  stdin,
		stdout: bufio.NewReaderSize(stdout, 64*1024),
		stderr: bufio.NewReaderSize(stderr, 8*1024),
		done:   make(chan struct{}),
	}

	// Drain stderr to slog (the SDK logs init / errors there).
	go p.drainStderr()

	return p, nil
}

func (p *Process) drainStderr() {
	for {
		line, err := p.stderr.ReadString('\n')
		if line != "" {
			slog.Debug("bridge_stderr", "line", line)
		}
		if err != nil {
			return
		}
	}
}

// SendStart writes the initial StartConfig JSON line to the bridge's stdin.
// Must be the first thing sent.
func (p *Process) SendStart(cfg StartConfig) error {
	return p.writeJSON(cfg)
}

// SendToolResult forwards an Aunic tool result back to the bridge in response
// to a prior tool_call event.
func (p *Process) SendToolResult(id, jsonResult string, isError bool) error {
	return p.writeJSON(ToolResult{Type: "tool_result", ID: id, JSON: jsonResult, IsError: isError})
}

// SendAbort tells the bridge to cancel the in-flight query.
func (p *Process) SendAbort() error {
	return p.writeJSON(Abort{Type: "abort"})
}

func (p *Process) writeJSON(v any) error {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Errorf("bridge: marshal: %w", err)
	}
	b = append(b, '\n')

	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return fmt.Errorf("bridge: stdin closed")
	}
	_, err = p.stdin.Write(b)
	return err
}

// NextEvent reads and returns the next event from the bridge. Returns io.EOF
// when the bridge has exited and stdout is drained.
func (p *Process) NextEvent() (Event, error) {
	line, err := p.stdout.ReadBytes('\n')
	if len(line) > 0 {
		var ev Event
		if jerr := json.Unmarshal(line, &ev); jerr != nil {
			slog.Warn("bridge_bad_event", "line", string(line), "error", jerr.Error())
			return Event{}, jerr
		}
		return ev, nil
	}
	if err == io.EOF {
		return Event{}, io.EOF
	}
	return Event{}, err
}

// Close terminates the bridge process if it's still running and releases
// resources. Safe to call multiple times.
func (p *Process) Close() {
	p.doneOnce.Do(func() {
		close(p.done)
		p.closeMu.Lock()
		p.closed = true
		p.closeMu.Unlock()
		_ = p.stdin.Close()
		// Wait briefly for clean exit, then kill.
		exited := make(chan error, 1)
		go func() { exited <- p.cmd.Wait() }()
		select {
		case <-exited:
		case <-time.After(2 * time.Second):
			_ = p.cmd.Process.Signal(syscall.SIGTERM)
			select {
			case <-exited:
			case <-time.After(3 * time.Second):
				_ = p.cmd.Process.Kill()
				<-exited
			}
		}
	})
}

// CheckNode returns nil when `node` is on PATH. Used at startup to give
// a clear error when the bridge can't possibly work.
func CheckNode() error {
	_, err := exec.LookPath("node")
	if err != nil {
		return fmt.Errorf("node not found on PATH — install Node.js to use the Claude Agent SDK backend")
	}
	return nil
}

// ResolveBridgeDir returns the directory containing dist/bridge.js. Searches
// in order:
//
//  1. $AUNIC_BRIDGE_DIR (explicit override)
//  2. <binary-dir>/bridge/        (built binary case: binary in aunic/)
//  3. <binary-dir>/../bridge/     (alternate layout)
//
// Returns an empty string and error if nothing matches.
//
// TODO(install): aunic-build should copy the bridge/ directory (dist/ +
// node_modules/) next to the installed binary so that AUNIC_BRIDGE_DIR
// is not required in production. The relative paths above already handle
// that layout — the copy step is all that's missing.
func ResolveBridgeDir() (string, error) {
	if env := os.Getenv("AUNIC_BRIDGE_DIR"); env != "" {
		if dirHasBridge(env) {
			return env, nil
		}
		return "", fmt.Errorf("AUNIC_BRIDGE_DIR=%s but dist/bridge.js not found there", env)
	}
	exe, err := os.Executable()
	if err == nil {
		if resolved, e := filepath.EvalSymlinks(exe); e == nil {
			exe = resolved
		}
		baseDir := filepath.Dir(exe)
		for _, rel := range []string{"bridge", filepath.Join("..", "bridge")} {
			candidate := filepath.Join(baseDir, rel)
			if dirHasBridge(candidate) {
				return candidate, nil
			}
		}
	}
	return "", fmt.Errorf("could not locate bridge dir; set AUNIC_BRIDGE_DIR or run from aunic/")
}

func dirHasBridge(dir string) bool {
	_, err := os.Stat(filepath.Join(dir, "dist", "bridge.js"))
	return err == nil
}
