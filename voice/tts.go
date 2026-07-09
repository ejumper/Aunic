package voice

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Chunking thresholds (in bytes of UTF-8 text):
//
//	< shortLimit  → single chunk (no split needed, wait is short)
//	< midLimit    → two equal halves (concurrent; both ready before first finishes)
//	≥ midLimit    → N chunks of chunkTarget chars each (all concurrent from t=0)
const (
	shortLimit  = 100
	midLimit    = 300
	chunkTarget = 175 // target chars per chunk for long text; snapped to sentence boundary
	chunkSlack  = 25  // ±window around chunkTarget when scanning for a boundary
)

// Lifecycle bounds.
const (
	fetchTimeout = 90 * time.Second
	openTimeout  = 10 * time.Second
	maxPlayback  = 5 * time.Minute
)

// splitChunks divides text into 1, 2, or N chunks for concurrent TTS fetching.
//
// Tier 1 (< 100 chars):  whole text — no benefit to splitting.
// Tier 2 (100–299 chars): two halves — concurrent fetch, both ready before first plays.
// Tier 3 (≥ 300 chars):  N × ~175-char chunks snapped to sentence boundaries —
//
//	all fetched concurrently from t=0; first audio starts as soon as chunk 1
//	is ready regardless of total length.
func splitChunks(s string) []string {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	switch {
	case len(s) < shortLimit:
		return []string{s}
	case len(s) < midLimit:
		return splitTwo(s)
	default:
		return splitMany(s)
	}
}

// splitTwo splits s into two roughly equal halves at the nearest sentence
// boundary to the midpoint, falling back to a word boundary.
func splitTwo(s string) []string {
	mid := len(s) / 2
	// Scan ±30 chars around the midpoint for a sentence end.
	lo := max(mid-30, 1)
	hi := min(mid+30, len(s)-1)
	// Prefer the boundary closest to mid; scan outward alternately.
	for d := 0; d <= hi-mid; d++ {
		for _, off := range []int{mid + d, mid - d} {
			if off < lo || off >= hi {
				continue
			}
			if isSentenceEnd(s, off) {
				return trimmedPair(s, off+1)
			}
		}
	}
	// Fall back to word boundary nearest the mid.
	for d := 0; d <= hi-lo; d++ {
		for _, off := range []int{mid + d, mid - d} {
			if off < lo || off >= hi {
				continue
			}
			if s[off] == ' ' {
				return trimmedPair(s, off+1)
			}
		}
	}
	return []string{s}
}

// splitMany splits s into sequential chunks of ~chunkTarget chars each,
// snapping each boundary to the nearest sentence end within ±chunkSlack.
func splitMany(s string) []string {
	var chunks []string
	for len(s) > 0 {
		if len(s) <= chunkTarget+chunkSlack {
			chunks = append(chunks, strings.TrimSpace(s))
			break
		}
		split := findBoundary(s, chunkTarget, chunkSlack)
		chunk := strings.TrimSpace(s[:split])
		if chunk != "" {
			chunks = append(chunks, chunk)
		}
		s = strings.TrimSpace(s[split:])
	}
	return chunks
}

// findBoundary returns a split index near target (within ±slack) that lands on
// a sentence boundary, falling back to a word boundary, falling back to target.
func findBoundary(s string, target, slack int) int {
	lo := max(target-slack, 1)
	hi := min(target+slack, len(s)-1)
	// Scan forward from target for sentence end.
	for i := target; i <= hi; i++ {
		if isSentenceEnd(s, i) {
			return i + 1
		}
	}
	// Scan backward from target.
	for i := target - 1; i >= lo; i-- {
		if isSentenceEnd(s, i) {
			return i + 1
		}
	}
	// Word boundary — scan forward then backward.
	for i := target; i <= hi; i++ {
		if s[i] == ' ' {
			return i + 1
		}
	}
	for i := target - 1; i >= lo; i-- {
		if s[i] == ' ' {
			return i + 1
		}
	}
	return target
}

// isSentenceEnd reports whether s[i] is a sentence-ending character followed
// by whitespace or end-of-string, or a bare newline.
func isSentenceEnd(s string, i int) bool {
	if i < 0 || i >= len(s) {
		return false
	}
	c := s[i]
	if c == '\n' {
		return true
	}
	if c == '.' || c == '!' || c == '?' {
		next := i + 1
		return next >= len(s) || s[next] == ' ' || s[next] == '\n'
	}
	return false
}

// trimmedPair splits s at pos and returns two trimmed, non-empty halves.
// If either half is empty after trimming, returns the whole text unsplit.
func trimmedPair(s string, pos int) []string {
	p1 := strings.TrimSpace(s[:pos])
	p2 := strings.TrimSpace(s[pos:])
	if p1 == "" || p2 == "" {
		return []string{strings.TrimSpace(s)}
	}
	return []string{p1, p2}
}

// fetchTTS sends text to the TTS server and returns the raw PCM bytes.
// Bounded by fetchTimeout so a stalled server never hangs the playback goroutine.
// Returns nil on any error.
func fetchTTS(text, endpoint, voiceName string) []byte {
	if text == "" {
		return nil
	}
	body, _ := json.Marshal(map[string]any{
		"input":           text,
		"voice":           "clone:" + voiceName,
		"model":           "tts-1-en",
		"stream":          true,
		"response_format": "pcm",
		"temperature":     0.4,
	})
	ctx, cancel := context.WithTimeout(context.Background(), fetchTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		endpoint+"/v1/audio/speech", bytes.NewReader(body))
	if err != nil {
		return nil
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	pcm, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil
	}
	return pcm
}

// openFifoWriteFD opens the FIFO write end without blocking indefinitely.
// Polls with O_NONBLOCK until mpv opens the read end or the deadline elapses,
// then switches back to blocking mode for sequential writes.
func openFifoWriteFD(path string, deadline time.Duration) (int, error) {
	end := time.Now().Add(deadline)
	for {
		fd, err := syscall.Open(path, syscall.O_WRONLY|syscall.O_NONBLOCK, 0)
		if err == nil {
			_ = syscall.SetNonblock(fd, false)
			return fd, nil
		}
		if err == syscall.ENXIO && time.Now().Before(end) {
			time.Sleep(20 * time.Millisecond)
			continue
		}
		return -1, err
	}
}

// writeAll writes b to fd, retrying EINTR and stopping on any other error
// (notably EPIPE when mpv has exited or been killed).
func writeAll(fd int, b []byte) {
	for len(b) > 0 {
		n, err := syscall.Write(fd, b)
		if err == syscall.EINTR {
			continue
		}
		if err != nil {
			return
		}
		b = b[n:]
	}
}

// Speak splits text into N chunks (1, 2, or many — see splitChunks), launches
// all TTS fetches concurrently from t=0, then plays them back-to-back through
// a single mpv instance writing to a FIFO.
//
// Audio starts as soon as chunk 1 is ready; subsequent chunks are written in
// order as each resolves. With a parallel TTS server all chunks generate
// simultaneously, so playback is seamless regardless of total text length.
//
// The returned *exec.Cmd is the mpv process. Callers may Process.Kill() it to
// stop playback; all cleanup (reaping mpv, removing the FIFO) is handled
// internally on every exit path.
func Speak(text, endpoint, voiceName string) (*exec.Cmd, error) {
	chunks := splitChunks(text)
	if len(chunks) == 0 {
		return nil, fmt.Errorf("tts: empty text")
	}

	fifo, err := os.CreateTemp("", "aunic-tts-*.fifo")
	if err != nil {
		return nil, fmt.Errorf("tts: temp file: %w", err)
	}
	fifoPath := fifo.Name()
	fifo.Close()
	_ = os.Remove(fifoPath)

	if err := syscall.Mkfifo(fifoPath, 0o600); err != nil {
		return nil, fmt.Errorf("tts: mkfifo: %w", err)
	}

	devNull, err := os.OpenFile(os.DevNull, os.O_RDWR, 0)
	if err != nil {
		_ = os.Remove(fifoPath)
		return nil, fmt.Errorf("tts: devnull: %w", err)
	}

	mpv := exec.Command("mpv",
		"--no-terminal",
		"--demuxer=rawaudio",
		"--demuxer-rawaudio-rate=24000",
		"--demuxer-rawaudio-channels=1",
		"--demuxer-rawaudio-format=s16le",
		fifoPath,
	)
	mpv.Stdin = devNull
	mpv.Stdout = devNull
	mpv.Stderr = devNull
	if err := mpv.Start(); err != nil {
		devNull.Close()
		_ = os.Remove(fifoPath)
		return nil, fmt.Errorf("tts: mpv: %w", err)
	}
	devNull.Close()

	go func() {
		// Backstop watchdog: force-kill mpv if the whole operation overruns
		// maxPlayback so mpv.Wait() can never block forever.
		done := make(chan struct{})
		go func() {
			select {
			case <-done:
			case <-time.After(maxPlayback):
				_ = mpv.Process.Kill()
			}
		}()

		defer os.Remove(fifoPath)
		defer close(done)
		defer mpv.Wait() //nolint:errcheck

		// Fire all chunk fetches concurrently from t=0.
		pcmChs := make([]chan []byte, len(chunks))
		for i, chunk := range chunks {
			ch := make(chan []byte, 1)
			pcmChs[i] = ch
			go func(t string, c chan []byte) {
				c <- fetchTTS(t, endpoint, voiceName)
			}(chunk, ch)
		}

		// Open the FIFO write end (waits for mpv to open the read end).
		fd, err := openFifoWriteFD(fifoPath, openTimeout)
		if err != nil {
			// mpv never opened the read end — drain channels and exit.
			for _, ch := range pcmChs {
				<-ch
			}
			_ = mpv.Process.Kill()
			return
		}
		defer syscall.Close(fd)

		// Write chunks in order as each resolves. Chunk 1 unblocks as soon as
		// its fetch completes; playback begins immediately. Subsequent chunks
		// are written as they arrive — if the server is parallel they arrive
		// quickly; if serial they arrive just in time (or with a brief gap).
		for _, ch := range pcmChs {
			if pcm := <-ch; len(pcm) > 0 {
				writeAll(fd, pcm)
			}
		}
	}()

	return mpv, nil
}

// FetchBytes converts text to PCM audio and returns the raw bytes.
// Uses the same concurrent chunking strategy as Speak but returns bytes
// instead of streaming to mpv. Returns an error if all chunks fail.
func FetchBytes(text, endpoint, voiceName string) ([]byte, error) {
	chunks := splitChunks(text)
	if len(chunks) == 0 {
		return nil, fmt.Errorf("tts: empty text")
	}

	pcmChs := make([]chan []byte, len(chunks))
	for i, chunk := range chunks {
		ch := make(chan []byte, 1)
		pcmChs[i] = ch
		go func(t string, c chan []byte) {
			c <- fetchTTS(t, endpoint, voiceName)
		}(chunk, ch)
	}

	var buf bytes.Buffer
	anyOK := false
	for _, ch := range pcmChs {
		if pcm := <-ch; len(pcm) > 0 {
			buf.Write(pcm)
			anyOK = true
		}
	}
	if !anyOK {
		return nil, fmt.Errorf("tts: all chunks failed")
	}
	return buf.Bytes(), nil
}

// SweepOrphans kills mpv instances and removes FIFOs left over from prior
// aunic sessions. Safe to call once at startup.
func SweepOrphans() {
	_ = exec.Command("pkill", "-f", "mpv.*aunic-tts-").Run()
	matches, _ := filepath.Glob(filepath.Join(os.TempDir(), "aunic-tts-*.fifo"))
	for _, p := range matches {
		_ = os.Remove(p)
	}
}
