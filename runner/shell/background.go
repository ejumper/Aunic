package shell

import (
	"bytes"
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"time"
)

const (
	maxBackgroundJobs            = 50
	completedJobRetentionMinutes = 8 * 60
)

// syncBuffer is a thread-safe bytes.Buffer.
type syncBuffer struct {
	buf bytes.Buffer
	mu  sync.RWMutex
}

func (sb *syncBuffer) Write(p []byte) (int, error) {
	sb.mu.Lock()
	defer sb.mu.Unlock()
	return sb.buf.Write(p)
}

func (sb *syncBuffer) String() string {
	sb.mu.RLock()
	defer sb.mu.RUnlock()
	return sb.buf.String()
}

// BackgroundShell is a shell running in the background.
type BackgroundShell struct {
	ID          string
	Command     string
	Description string
	WorkingDir  string
	ctx         context.Context
	cancel      context.CancelFunc
	stdout      *syncBuffer
	stderr      *syncBuffer
	done        chan struct{}
	exitErr     error
	completedAt atomic.Int64 // unix timestamp when done, 0 if still running
}

// GetOutput returns the current captured output and whether the shell has finished.
func (bs *BackgroundShell) GetOutput() (stdout, stderr string, done bool, err error) {
	select {
	case <-bs.done:
		return bs.stdout.String(), bs.stderr.String(), true, bs.exitErr
	default:
		return bs.stdout.String(), bs.stderr.String(), false, nil
	}
}

// IsDone reports whether the shell has finished.
func (bs *BackgroundShell) IsDone() bool {
	select {
	case <-bs.done:
		return true
	default:
		return false
	}
}

// Wait blocks until the shell exits.
func (bs *BackgroundShell) Wait() { <-bs.done }

// WaitContext blocks until the shell exits or ctx is done.
// Returns true if the shell exited, false if ctx expired first.
func (bs *BackgroundShell) WaitContext(ctx context.Context) bool {
	select {
	case <-bs.done:
		return true
	case <-ctx.Done():
		return false
	}
}

// shellMap is a mutex-protected map[string]*BackgroundShell.
type shellMap struct {
	mu sync.Mutex
	m  map[string]*BackgroundShell
}

func newShellMap() *shellMap { return &shellMap{m: make(map[string]*BackgroundShell)} }

func (s *shellMap) set(k string, v *BackgroundShell) {
	s.mu.Lock(); defer s.mu.Unlock(); s.m[k] = v
}
func (s *shellMap) get(k string) (*BackgroundShell, bool) {
	s.mu.Lock(); defer s.mu.Unlock(); v, ok := s.m[k]; return v, ok
}
func (s *shellMap) take(k string) (*BackgroundShell, bool) {
	s.mu.Lock(); defer s.mu.Unlock()
	v, ok := s.m[k]
	if ok {
		delete(s.m, k)
	}
	return v, ok
}
func (s *shellMap) len() int {
	s.mu.Lock(); defer s.mu.Unlock(); return len(s.m)
}
func (s *shellMap) values() []*BackgroundShell {
	s.mu.Lock(); defer s.mu.Unlock()
	out := make([]*BackgroundShell, 0, len(s.m))
	for _, v := range s.m {
		out = append(out, v)
	}
	return out
}
func (s *shellMap) ids() []string {
	s.mu.Lock(); defer s.mu.Unlock()
	out := make([]string, 0, len(s.m))
	for k := range s.m {
		out = append(out, k)
	}
	return out
}
func (s *shellMap) reset() {
	s.mu.Lock(); defer s.mu.Unlock(); s.m = make(map[string]*BackgroundShell)
}

// BackgroundShellManager manages background shell instances.
type BackgroundShellManager struct {
	shells *shellMap
}

var (
	bgManager     *BackgroundShellManager
	bgManagerOnce sync.Once
	idCounter     atomic.Uint64
)

// GetBackgroundShellManager returns the process-wide singleton manager.
func GetBackgroundShellManager() *BackgroundShellManager {
	bgManagerOnce.Do(func() {
		bgManager = &BackgroundShellManager{shells: newShellMap()}
	})
	return bgManager
}

// Start creates and launches a new background shell.
func (m *BackgroundShellManager) Start(ctx context.Context, workingDir string, blockFuncs []BlockFunc, command, description string) (*BackgroundShell, error) {
	if m.shells.len() >= maxBackgroundJobs {
		return nil, fmt.Errorf("maximum background jobs (%d) reached; wait for some to finish", maxBackgroundJobs)
	}

	id := fmt.Sprintf("%03X", idCounter.Add(1))
	sh := NewShell(&Options{WorkingDir: workingDir, BlockFuncs: blockFuncs})
	shellCtx, cancel := context.WithCancel(ctx)

	bs := &BackgroundShell{
		ID:          id,
		Command:     command,
		Description: description,
		WorkingDir:  workingDir,
		ctx:         shellCtx,
		cancel:      cancel,
		stdout:      &syncBuffer{},
		stderr:      &syncBuffer{},
		done:        make(chan struct{}),
	}
	m.shells.set(id, bs)

	go func() {
		defer close(bs.done)
		bs.exitErr = sh.ExecStream(shellCtx, command, bs.stdout, bs.stderr)
		bs.completedAt.Store(time.Now().Unix())
	}()

	return bs, nil
}

// Get retrieves a background shell by ID.
func (m *BackgroundShellManager) Get(id string) (*BackgroundShell, bool) {
	return m.shells.get(id)
}

// Remove removes a completed shell from tracking without killing it.
func (m *BackgroundShellManager) Remove(id string) error {
	_, ok := m.shells.take(id)
	if !ok {
		return fmt.Errorf("background shell not found: %s", id)
	}
	return nil
}

// Kill terminates and removes a background shell by ID.
func (m *BackgroundShellManager) Kill(id string) error {
	bs, ok := m.shells.take(id)
	if !ok {
		return fmt.Errorf("background shell not found: %s", id)
	}
	bs.cancel()
	<-bs.done
	return nil
}

// List returns all tracked shell IDs.
func (m *BackgroundShellManager) List() []string { return m.shells.ids() }

// Cleanup removes shells that completed more than the retention period ago.
func (m *BackgroundShellManager) Cleanup() int {
	now := time.Now().Unix()
	retention := int64(completedJobRetentionMinutes * 60)
	var toRemove []string
	for _, bs := range m.shells.values() {
		if t := bs.completedAt.Load(); t > 0 && now-t > retention {
			toRemove = append(toRemove, bs.ID)
		}
	}
	for _, id := range toRemove {
		m.shells.take(id)
	}
	return len(toRemove)
}

// KillAll terminates all background shells, waiting up to ctx for each.
func (m *BackgroundShellManager) KillAll(ctx context.Context) {
	shells := m.shells.values()
	m.shells.reset()
	var wg sync.WaitGroup
	for _, bs := range shells {
		wg.Add(1)
		go func(bs *BackgroundShell) {
			defer wg.Done()
			bs.cancel()
			select {
			case <-bs.done:
			case <-ctx.Done():
			}
		}(bs)
	}
	wg.Wait()
}
