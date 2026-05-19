// Package shell provides cross-platform POSIX shell execution with command blocking.
// Ported from crush/internal/shell, adapted for aunic.
package shell

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"slices"
	"strings"
	"sync"

	"mvdan.cc/sh/v3/expand"
	"mvdan.cc/sh/v3/interp"
	"mvdan.cc/sh/v3/syntax"
)

// BlockFunc is a function that determines if a command should be blocked.
type BlockFunc func(args []string) bool

// Shell provides cross-platform shell execution using a POSIX interpreter.
type Shell struct {
	env        []string
	cwd        string
	mu         sync.Mutex
	blockFuncs []BlockFunc
}

// Options for creating a new Shell.
type Options struct {
	WorkingDir string
	Env        []string
	BlockFuncs []BlockFunc
}

// NewShell creates a new Shell with the given options.
func NewShell(opts *Options) *Shell {
	if opts == nil {
		opts = &Options{}
	}
	cwd := opts.WorkingDir
	if cwd == "" {
		cwd, _ = os.Getwd()
	}
	env := opts.Env
	if env == nil {
		env = os.Environ()
	}
	env = append(env, "AUNIC=1", "AGENT=aunic", "AI_AGENT=aunic")
	return &Shell{cwd: cwd, env: env, blockFuncs: opts.BlockFuncs}
}

// Exec executes a command and returns stdout, stderr, and an error.
func (s *Shell) Exec(ctx context.Context, command string) (string, string, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var stdout, stderr bytes.Buffer
	err := s.execCommon(ctx, command, &stdout, &stderr)
	return stdout.String(), stderr.String(), err
}

// ExecStream executes a command, streaming output to the provided writers.
func (s *Shell) ExecStream(ctx context.Context, command string, stdout, stderr io.Writer) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.execCommon(ctx, command, stdout, stderr)
}

// GetWorkingDir returns the current working directory.
func (s *Shell) GetWorkingDir() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.cwd
}

func (s *Shell) execCommon(ctx context.Context, command string, stdout, stderr io.Writer) (err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("command execution panic: %v", r)
		}
	}()

	node, parseErr := syntax.NewParser().Parse(strings.NewReader(command), "")
	if parseErr != nil {
		return fmt.Errorf("could not parse command: %w", parseErr)
	}

	runner, newErr := s.newInterp(stdout, stderr)
	if newErr != nil {
		return fmt.Errorf("could not create interpreter: %w", newErr)
	}

	return runner.Run(ctx, node)
}

func (s *Shell) newInterp(stdout, stderr io.Writer) (*interp.Runner, error) {
	return interp.New(
		interp.StdIO(nil, stdout, stderr),
		interp.Interactive(false),
		interp.Env(expand.ListEnviron(s.env...)),
		interp.Dir(s.cwd),
		interp.ExecHandlers(s.blockHandler()),
	)
}

func (s *Shell) blockHandler() func(next interp.ExecHandlerFunc) interp.ExecHandlerFunc {
	return func(next interp.ExecHandlerFunc) interp.ExecHandlerFunc {
		return func(ctx context.Context, args []string) error {
			if len(args) == 0 {
				return next(ctx, args)
			}
			for _, bf := range s.blockFuncs {
				if bf(args) {
					return fmt.Errorf("command is not allowed for security reasons: %q", args[0])
				}
			}
			return next(ctx, args)
		}
	}
}

// CommandsBlocker returns a BlockFunc that blocks exact command name matches.
func CommandsBlocker(cmds []string) BlockFunc {
	banned := make(map[string]struct{}, len(cmds))
	for _, c := range cmds {
		banned[c] = struct{}{}
	}
	return func(args []string) bool {
		if len(args) == 0 {
			return false
		}
		_, ok := banned[args[0]]
		return ok
	}
}

// ArgumentsBlocker returns a BlockFunc that blocks a specific subcommand/flag combination.
// args must be the required positional arguments (in order); flags must all be present.
func ArgumentsBlocker(cmd string, args []string, flags []string) BlockFunc {
	return func(parts []string) bool {
		if len(parts) == 0 || parts[0] != cmd {
			return false
		}
		argParts, flagParts := splitArgsFlags(parts[1:])
		if len(argParts) < len(args) || len(flagParts) < len(flags) {
			return false
		}
		return slices.Equal(argParts[:len(args)], args) && isSubset(flags, flagParts)
	}
}

func splitArgsFlags(parts []string) (args []string, flags []string) {
	for _, p := range parts {
		if strings.HasPrefix(p, "-") {
			flag := p
			if before, _, ok := strings.Cut(p, "="); ok {
				flag = before
			}
			flags = append(flags, flag)
		} else {
			args = append(args, p)
		}
	}
	return
}

func isSubset(needles, haystack []string) bool {
	set := make(map[string]struct{}, len(haystack))
	for _, h := range haystack {
		set[h] = struct{}{}
	}
	for _, n := range needles {
		if _, ok := set[n]; !ok {
			return false
		}
	}
	return true
}

// IsInterrupt reports whether err is a context cancellation or deadline.
func IsInterrupt(err error) bool {
	return errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
}

// ExitCode extracts the numeric exit code from a shell error.
func ExitCode(err error) int {
	if err == nil {
		return 0
	}
	var exitErr interp.ExitStatus
	if errors.As(err, &exitErr) {
		return int(exitErr)
	}
	return 1
}
