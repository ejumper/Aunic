package voice

import (
	"os"
	"os/exec"
	"path/filepath"
)

// SweepOrphans kills mpv instances and removes FIFOs left over from prior
// aunic sessions. Safe to call once at startup.
func SweepOrphans() {
	_ = exec.Command("pkill", "-f", "mpv.*aunic-tts-").Run()
	matches, _ := filepath.Glob(filepath.Join(os.TempDir(), "aunic-tts-*.fifo"))
	for _, p := range matches {
		_ = os.Remove(p)
	}
}
