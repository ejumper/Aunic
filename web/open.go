package web

import (
	"fmt"
	"net/url"
	"os/exec"
	"runtime"
)

// Open opens rawURL in the user's default browser. The launch is non-blocking
// — the helper returns once the OS handler has been spawned (or an error
// detected) without waiting for the browser to actually open.
func Open(rawURL string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "linux":
		cmd = exec.Command("xdg-open", rawURL)
	case "darwin":
		cmd = exec.Command("open", rawURL)
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", rawURL)
	default:
		return fmt.Errorf("open: unsupported platform %s", runtime.GOOS)
	}
	return cmd.Start()
}

// SearchURL builds a DuckDuckGo search URL for query. Used to "open in browser"
// for transcript search-query cells.
func SearchURL(query string) string {
	return "https://duckduckgo.com/?q=" + url.QueryEscape(query)
}
