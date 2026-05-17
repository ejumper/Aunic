package web

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os/exec"
	"strings"
)

// ErrDdgrNotFound is returned by Search when ddgr is not on PATH.
var ErrDdgrNotFound = errors.New("ddgr not found in PATH")

// Result is a single DuckDuckGo search result.
type Result struct {
	Title    string
	URL      string
	Domain   string
	Abstract string
}

// Search runs ddgr to get n results for query. n must be between 1 and 25.
// Returns ErrDdgrNotFound if ddgr is not installed.
func Search(ctx context.Context, query string, n int) ([]Result, error) {
	if _, err := exec.LookPath("ddgr"); err != nil {
		return nil, ErrDdgrNotFound
	}

	if n < 1 {
		n = 1
	}
	if n > 25 {
		n = 25
	}

	cmd := exec.CommandContext(ctx, "ddgr",
		"--json",
		"--np",
		"-n", fmt.Sprintf("%d", n),
		query,
	)

	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return nil, fmt.Errorf("ddgr exited with code %d: %s", exitErr.ExitCode(), strings.TrimSpace(string(exitErr.Stderr)))
		}
		return nil, fmt.Errorf("ddgr: %w", err)
	}

	var raw []struct {
		Title    string `json:"title"`
		URL      string `json:"url"`
		Abstract string `json:"abstract"`
	}
	if err := json.Unmarshal(out, &raw); err != nil {
		return nil, fmt.Errorf("parsing ddgr output: %w", err)
	}

	results := make([]Result, 0, len(raw))
	for _, r := range raw {
		results = append(results, Result{
			Title:    r.Title,
			URL:      r.URL,
			Domain:   extractDomain(r.URL),
			Abstract: r.Abstract,
		})
	}
	return results, nil
}

func extractDomain(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}
	host := u.Hostname()
	host = strings.TrimPrefix(host, "www.")
	return host
}
