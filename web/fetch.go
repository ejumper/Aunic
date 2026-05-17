package web

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	htmltomarkdown "github.com/JohannesKaufmann/html-to-markdown/v2"
	readability "github.com/go-shiori/go-readability"
)

const (
	fetchTimeout  = 10 * time.Second
	maxBodyBytes  = 2 * 1024 * 1024 // 2 MB
	userAgent     = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

// Page is the result of fetching and converting a web page.
type Page struct {
	Title    string
	URL      string
	Markdown string
}

// Fetch downloads the page at rawURL, extracts the main article content via
// go-readability, and converts it to Markdown. The caller's ctx controls
// cancellation; an independent 10-second timeout is also applied.
func Fetch(ctx context.Context, rawURL string) (Page, error) {
	ctx, cancel := context.WithTimeout(ctx, fetchTimeout)
	defer cancel()

	parsedURL, err := url.Parse(rawURL)
	if err != nil {
		return Page{}, fmt.Errorf("invalid URL: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return Page{}, fmt.Errorf("building request: %w", err)
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "text/html,application/xhtml+xml")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return Page{}, fmt.Errorf("fetching %s: %w", rawURL, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return Page{}, fmt.Errorf("HTTP %d from %s", resp.StatusCode, rawURL)
	}

	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBodyBytes))
	if err != nil {
		return Page{}, fmt.Errorf("reading body: %w", err)
	}

	parser := readability.NewParser()
	article, err := parser.Parse(strings.NewReader(string(body)), parsedURL)
	if err != nil {
		return Page{}, fmt.Errorf("readability: %w", err)
	}

	content := article.Content
	if strings.TrimSpace(content) == "" {
		// Readability found nothing; fall back to raw HTML.
		content = string(body)
	}

	md, err := htmltomarkdown.ConvertString(content)
	if err != nil {
		return Page{}, fmt.Errorf("html-to-markdown: %w", err)
	}

	title := article.Title
	if title == "" {
		title = parsedURL.Host
	}

	return Page{
		Title:    title,
		URL:      rawURL,
		Markdown: md,
	}, nil
}
