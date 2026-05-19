package runner

import (
	"context"
	_ "embed"
	"encoding/json"
	"fmt"
	"log/slog"
	"path/filepath"
	"strings"
	"time"

	"github.com/ejumper/aunic/runner/shell"
)

//go:embed desc_bash.md
var bashDescTemplate string

const (
	bashDefaultAutoBackground = 60 // seconds
	bashMaxOutputLength       = 30000
	bashNoOutput              = "no output"
)

var bannedCommands = []string{
	// Network / download tools
	"alias", "aria2c", "axel", "chrome", "curl", "curlie", "firefox",
	"http-prompt", "httpie", "links", "lynx", "nc", "safari", "scp",
	"ssh", "telnet", "w3m", "wget", "xh",
	// Privilege escalation
	"doas", "su", "sudo",
	// Package managers
	"apk", "apt", "apt-cache", "apt-get", "dnf", "dpkg", "emerge",
	"home-manager", "makepkg", "opkg", "pacman", "paru", "pkg",
	"pkg_add", "pkg_delete", "portage", "rpm", "yay", "yum", "zypper",
	// System modification
	"at", "batch", "chkconfig", "crontab", "fdisk", "mkfs", "mount",
	"parted", "service", "systemctl", "umount",
	// Network configuration
	"firewall-cmd", "ifconfig", "ip", "iptables", "netstat",
	"pfctl", "route", "ufw",
}

func bashBlockFuncs() []shell.BlockFunc {
	return []shell.BlockFunc{
		shell.CommandsBlocker(bannedCommands),

		// System package managers
		shell.ArgumentsBlocker("apk", []string{"add"}, nil),
		shell.ArgumentsBlocker("apt", []string{"install"}, nil),
		shell.ArgumentsBlocker("apt-get", []string{"install"}, nil),
		shell.ArgumentsBlocker("dnf", []string{"install"}, nil),
		shell.ArgumentsBlocker("pacman", nil, []string{"-S"}),
		shell.ArgumentsBlocker("pkg", []string{"install"}, nil),
		shell.ArgumentsBlocker("yum", []string{"install"}, nil),
		shell.ArgumentsBlocker("zypper", []string{"install"}, nil),

		// Language package managers
		shell.ArgumentsBlocker("brew", []string{"install"}, nil),
		shell.ArgumentsBlocker("cargo", []string{"install"}, nil),
		shell.ArgumentsBlocker("gem", []string{"install"}, nil),
		shell.ArgumentsBlocker("go", []string{"install"}, nil),
		shell.ArgumentsBlocker("npm", []string{"install"}, []string{"--global"}),
		shell.ArgumentsBlocker("npm", []string{"install"}, []string{"-g"}),
		shell.ArgumentsBlocker("pip", []string{"install"}, []string{"--user"}),
		shell.ArgumentsBlocker("pip3", []string{"install"}, []string{"--user"}),
		shell.ArgumentsBlocker("pnpm", []string{"add"}, []string{"--global"}),
		shell.ArgumentsBlocker("pnpm", []string{"add"}, []string{"-g"}),
		shell.ArgumentsBlocker("yarn", []string{"global", "add"}, nil),

		// `go test -exec` can run arbitrary commands
		shell.ArgumentsBlocker("go", []string{"test"}, []string{"-exec"}),
	}
}

func bashDescription() string {
	return fmt.Sprintf(bashDescTemplate, bashDefaultAutoBackground, bashMaxOutputLength, strings.Join(bannedCommands, ", "))
}

type bashArgs struct {
	Description         string `json:"description"`
	Command             string `json:"command"`
	WorkingDir          string `json:"working_dir,omitempty"`
	RunInBackground     bool   `json:"run_in_background,omitempty"`
	AutoBackgroundAfter int    `json:"auto_background_after,omitempty"`
}

type bashTool struct{}

func (bashTool) Name() string        { return "Bash" }
func (bashTool) Description() string { return bashDescription() }
func (bashTool) Schema() map[string]any {
	return map[string]any{
		"type": "object",
		"properties": map[string]any{
			"description":           map[string]any{"type": "string", "description": "Brief description of what the command does (30 chars or less)."},
			"command":               map[string]any{"type": "string", "description": "The shell command to execute."},
			"working_dir":           map[string]any{"type": "string", "description": "Working directory for the command (absolute path). Defaults to the note's directory."},
			"run_in_background":     map[string]any{"type": "boolean", "description": "Run the command in a background shell immediately."},
			"auto_background_after": map[string]any{"type": "integer", "description": fmt.Sprintf("Seconds to wait before auto-backgrounding. Default %d.", bashDefaultAutoBackground)},
		},
		"required":             []string{"command"},
		"additionalProperties": false,
	}
}

func (bashTool) Execute(ctx context.Context, rc *RunContext, argsJSON string) Result {
	var args bashArgs
	if err := json.Unmarshal([]byte(argsJSON), &args); err != nil {
		return errorResult("invalid_arguments", err.Error())
	}
	if args.Command == "" {
		return errorResult("empty_command", "command must not be empty.")
	}

	workDir := filepath.Dir(rc.ActivePath)
	if args.WorkingDir != "" {
		workDir = args.WorkingDir
	}

	// Check if the command is in the safe (no-permission-required) list
	cmdLower := strings.ToLower(args.Command)
	isSafe := false
	for _, safe := range safeCommands {
		if strings.HasPrefix(cmdLower, safe) {
			rest := cmdLower[len(safe):]
			if rest == "" || rest[0] == ' ' || rest[0] == '-' {
				isSafe = true
				break
			}
		}
	}
	_ = isSafe // permission checking not yet implemented; always proceed

	bgMgr := shell.GetBackgroundShellManager()
	bgMgr.Cleanup()

	slog.Info("bash_execute", "command", descOrCmd(args.Description, args.Command), "working_dir", workDir, "background", args.RunInBackground)

	// Immediate background execution
	if args.RunInBackground {
		startTime := time.Now()
		bs, err := bgMgr.Start(context.Background(), workDir, bashBlockFuncs(), args.Command, args.Description)
		if err != nil {
			slog.Error("bash_error", "error", err.Error())
			return errorResult("start_failed", err.Error())
		}

		// Brief wait to catch fast failures (blocked commands, syntax errors).
		// Honor ctx so a run-cancel during this window doesn't hang.
		select {
		case <-time.After(time.Second):
		case <-ctx.Done():
			bgMgr.Kill(bs.ID)
			return errorResult("cancelled", "run cancelled during bash startup.")
		}
		stdout, stderr, done, execErr := bs.GetOutput()

		if done {
			bgMgr.Remove(bs.ID)
			output := formatBashOutput(stdout, stderr, execErr)
			slog.Info("bash_result", "exit_code", shell.ExitCode(execErr), "duration", time.Since(startTime), "background", false)
			b, _ := json.Marshal(bashResult(output, args.Description, bs.WorkingDir, startTime, false, ""))
			if output == "" {
				output = bashNoOutput
			}
			return Result{JSON: string(b), Summary: descOrCmd(args.Description, args.Command)}
		}

		slog.Info("bash_result", "shell_id", bs.ID, "background", true)
		b, _ := json.Marshal(bashResult("", args.Description, bs.WorkingDir, startTime, true, bs.ID))
		return Result{
			JSON:    string(b),
			Summary: fmt.Sprintf("[background %s] %s", bs.ID, descOrCmd(args.Description, args.Command)),
		}
	}

	// Synchronous execution with auto-background fallback
	startTime := time.Now()
	bs, err := bgMgr.Start(context.Background(), workDir, bashBlockFuncs(), args.Command, args.Description)
	if err != nil {
		slog.Error("bash_error", "error", err.Error())
		return errorResult("start_failed", err.Error())
	}

	autoAfter := time.Duration(bashDefaultAutoBackground) * time.Second
	if args.AutoBackgroundAfter > 0 {
		autoAfter = time.Duration(args.AutoBackgroundAfter) * time.Second
	}

	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	timeout := time.After(autoAfter)

	var stdout, stderr string
	var done bool
	var execErr error

loop:
	for {
		select {
		case <-ticker.C:
			stdout, stderr, done, execErr = bs.GetOutput()
			if done {
				break loop
			}
		case <-timeout:
			stdout, stderr, done, execErr = bs.GetOutput()
			break loop
		case <-ctx.Done():
			bgMgr.Kill(bs.ID)
			return errorResult("cancelled", "run cancelled during bash execution.")
		}
	}

	if done {
		bgMgr.Remove(bs.ID)
		output := formatBashOutput(stdout, stderr, execErr)
		if output == "" {
			output = bashNoOutput
		}
		if shell.ExitCode(execErr) == 0 && !shell.IsInterrupt(execErr) && execErr != nil {
			return errorResult("exec_failed", fmt.Sprintf("command error: %v", execErr))
		}
		slog.Info("bash_result", "exit_code", shell.ExitCode(execErr), "duration", time.Since(startTime), "background", false)
		output += fmt.Sprintf("\n\n<cwd>%s</cwd>", workDir)
		b, _ := json.Marshal(bashResult(output, args.Description, bs.WorkingDir, startTime, false, ""))
		return Result{JSON: string(b), Summary: descOrCmd(args.Description, args.Command)}
	}

	// Timed out — keep as background job
	slog.Info("bash_result", "shell_id", bs.ID, "background", true, "duration", time.Since(startTime))
	b, _ := json.Marshal(bashResult("", args.Description, bs.WorkingDir, startTime, true, bs.ID))
	return Result{
		JSON:    string(b),
		Summary: fmt.Sprintf("[auto-background %s] %s", bs.ID, descOrCmd(args.Description, args.Command)),
	}
}

func bashResult(output, description, workingDir string, start time.Time, background bool, shellID string) map[string]any {
	m := map[string]any{
		"start_time":        start.UnixMilli(),
		"end_time":          time.Now().UnixMilli(),
		"description":       description,
		"working_directory": workingDir,
		"background":        background,
	}
	if output != "" {
		m["output"] = output
	}
	if shellID != "" {
		m["shell_id"] = shellID
	}
	return m
}

func descOrCmd(description, command string) string {
	if description != "" {
		return description
	}
	if len(command) > 60 {
		return command[:60] + "…"
	}
	return command
}

// formatBashOutput combines stdout and stderr with exit-code/interrupt information.
func formatBashOutput(stdout, stderr string, execErr error) string {
	stdout = truncateBashOutput(stdout)
	stderr = truncateBashOutput(stderr)

	errorMsg := stderr
	if errorMsg == "" && execErr != nil {
		errorMsg = execErr.Error()
	}

	if shell.IsInterrupt(execErr) {
		if errorMsg != "" {
			errorMsg += "\n"
		}
		errorMsg += "Command was aborted before completion"
	} else if shell.ExitCode(execErr) != 0 {
		if errorMsg != "" {
			errorMsg += "\n"
		}
		errorMsg += fmt.Sprintf("Exit code %d", shell.ExitCode(execErr))
	}

	out := stdout
	if out != "" && errorMsg != "" {
		out += "\n"
	}
	if errorMsg != "" {
		out += "\n" + errorMsg
	}
	return out
}

// truncateBashOutput trims content to bashMaxOutputLength, preserving head and tail.
func truncateBashOutput(content string) string {
	if len(content) <= bashMaxOutputLength {
		return content
	}
	half := bashMaxOutputLength / 2
	head := content[:half]
	tail := content[len(content)-half:]
	truncated := countBashLines(content[half : len(content)-half])
	return fmt.Sprintf("%s\n\n... [%d lines truncated] ...\n\n%s", head, truncated, tail)
}

func countBashLines(s string) int {
	if s == "" {
		return 0
	}
	return strings.Count(s, "\n") + 1
}
