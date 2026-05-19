package agent

import (
	"os"
	"os/exec"
	"strings"

	"github.com/atotto/clipboard"
	tea "github.com/charmbracelet/bubbletea"
)

// PasteImageMsg is delivered when the clipboard contains binary image data.
type PasteImageMsg struct {
	Data     []byte
	MimeType string // "image/png" or "image/jpeg"
}

// PasteTextMsg is delivered when the clipboard contains plain text.
type PasteTextMsg struct {
	Text string
}

// ReadClipboardCmd returns a tea.Cmd that reads the clipboard asynchronously.
// If image data is found it delivers PasteImageMsg; otherwise PasteTextMsg.
func ReadClipboardCmd() tea.Cmd {
	return func() tea.Msg {
		wayland := os.Getenv("WAYLAND_DISPLAY") != ""

		for _, mime := range []string{"image/png", "image/jpeg"} {
			data, err := readImageClipboard(wayland, mime)
			if err == nil && len(data) > 0 {
				return PasteImageMsg{Data: data, MimeType: mime}
			}
		}

		text, _ := clipboard.ReadAll()
		return PasteTextMsg{Text: text}
	}
}

func readImageClipboard(wayland bool, mime string) ([]byte, error) {
	var cmd *exec.Cmd
	if wayland {
		cmd = exec.Command("wl-paste", "--type", mime)
	} else {
		cmd = exec.Command("xclip", "-selection", "clipboard", "-t", mime, "-o")
	}
	out, err := cmd.Output()
	if err != nil {
		return nil, err
	}
	s := strings.TrimSpace(string(out))
	if s == "" {
		return nil, nil
	}
	return out, nil
}
