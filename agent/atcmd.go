package agent

import "strings"

// ParseAtFiles extracts all @path tokens from s. A token is @ followed by a
// non-space run that does not start with "[" (which would be an [image #N]
// token, not a file path). Returns paths in order found, deduplicated.
func ParseAtFiles(s string) []string {
	seen := make(map[string]bool)
	var paths []string
	i := 0
	for i < len(s) {
		idx := strings.IndexByte(s[i:], '@')
		if idx < 0 {
			break
		}
		pos := i + idx

		// @ must be preceded by start-of-string or whitespace.
		if pos > 0 && s[pos-1] != ' ' && s[pos-1] != '\t' && s[pos-1] != '\n' {
			i = pos + 1
			continue
		}

		// Character after @ must exist, not be whitespace, and not be "[".
		rest := pos + 1
		if rest >= len(s) || s[rest] == ' ' || s[rest] == '\t' || s[rest] == '\n' || s[rest] == '[' {
			i = pos + 1
			continue
		}

		// Consume until whitespace.
		end := rest
		for end < len(s) && s[end] != ' ' && s[end] != '\t' && s[end] != '\n' {
			end++
		}

		path := s[rest:end]
		if !seen[path] {
			seen[path] = true
			paths = append(paths, path)
		}
		i = end
	}
	return paths
}

// StripAtFiles removes all @path tokens from s, collapsing any resulting
// double spaces. The returned string has leading/trailing whitespace trimmed.
func StripAtFiles(s string) string {
	paths := ParseAtFiles(s)
	if len(paths) == 0 {
		return strings.TrimSpace(s)
	}
	for _, p := range paths {
		s = strings.ReplaceAll(s, "@"+p, "")
	}
	// Collapse multiple spaces.
	for strings.Contains(s, "  ") {
		s = strings.ReplaceAll(s, "  ", " ")
	}
	return strings.TrimSpace(s)
}

// ColorAtFiles injects ANSI blue (color 4) around each @path token in s.
// [image #N] tokens are colored cyan (color 6).
func ColorAtFiles(s string) string {
	if !strings.ContainsAny(s, "@[") {
		return s
	}
	var b strings.Builder
	i := 0
	for i < len(s) {
		// Check for [image #N] token.
		if s[i] == '[' && strings.HasPrefix(s[i:], "[image #") {
			end := strings.IndexByte(s[i:], ']')
			if end >= 0 {
				token := s[i : i+end+1]
				b.WriteString("\x1b[36m")
				b.WriteString(token)
				b.WriteString("\x1b[39m")
				i += end + 1
				continue
			}
		}

		// Check for @path token.
		if s[i] == '@' {
			// Must be at start or preceded by whitespace.
			preceded := i == 0 || s[i-1] == ' ' || s[i-1] == '\t' || s[i-1] == '\n'
			rest := i + 1
			isFilePath := preceded && rest < len(s) && s[rest] != ' ' && s[rest] != '\t' && s[rest] != '\n' && s[rest] != '['
			if isFilePath {
				end := rest
				for end < len(s) && s[end] != ' ' && s[end] != '\t' && s[end] != '\n' {
					end++
				}
				b.WriteString("\x1b[34m")
				b.WriteString(s[i:end])
				b.WriteString("\x1b[39m")
				i = end
				continue
			}
		}

		b.WriteByte(s[i])
		i++
	}
	return b.String()
}
