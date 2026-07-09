package voice

import (
	"bufio"
	"fmt"
	"os"
	"syscall"
)

const (
	pipeLinkPath = "/tmp/aunic-voice-current.pipe"
)

func pipePath(pid int) string {
	return fmt.Sprintf("/tmp/aunic-voice-%d.pipe", pid)
}

// OpenPipe creates the named FIFO for this instance, updates the symlink so
// voice-input.sh can find this session, and starts a goroutine that reads lines
// from the FIFO. Returns the line channel and a cleanup func.
//
// The cleanup func removes the FIFO and clears the symlink. Calling it closes
// the returned channel, which causes any waitForVoiceInput tea.Cmd to return
// nil and stop re-queuing.
func OpenPipe(pid int) (<-chan string, func(), error) {
	path := pipePath(pid)
	_ = os.Remove(path) // remove stale FIFO if any
	if err := syscall.Mkfifo(path, 0o600); err != nil {
		return nil, nil, fmt.Errorf("voice pipe: mkfifo: %w", err)
	}

	if err := ClaimSymlink(pid); err != nil {
		_ = os.Remove(path)
		return nil, nil, err
	}

	ch := make(chan string, 4)
	done := make(chan struct{})

	go func() {
		defer close(ch)
		for {
			select {
			case <-done:
				return
			default:
			}
			// Open blocks until a writer opens the write end. This is normal
			// FIFO semantics — voice-input.sh opening for write unblocks us.
			f, err := os.Open(path)
			if err != nil {
				return // FIFO removed by cleanup
			}
			scanner := bufio.NewScanner(f)
			for scanner.Scan() {
				line := scanner.Text()
				if line != "" {
					select {
					case ch <- line:
					case <-done:
						f.Close()
						return
					}
				}
			}
			f.Close()
			// Writer closed — loop to re-open for the next invocation.
		}
	}()

	release := func() {
		close(done)
		_ = os.Remove(path)
		// Clear the symlink only if it still points to this instance.
		target, err := os.Readlink(pipeLinkPath)
		if err == nil && target == path {
			_ = os.Remove(pipeLinkPath)
		}
	}
	return ch, release, nil
}

// ClaimSymlink atomically updates the symlink to point to this instance's pipe.
// Called on open and on every Ctrl+S save.
func ClaimSymlink(pid int) error {
	path := pipePath(pid)
	// Atomic replace: write to a temp name then rename.
	tmp := pipeLinkPath + ".tmp"
	_ = os.Remove(tmp)
	if err := os.Symlink(path, tmp); err != nil {
		return fmt.Errorf("voice pipe: symlink: %w", err)
	}
	return os.Rename(tmp, pipeLinkPath)
}
