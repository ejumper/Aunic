package tasks

import (
	"bufio"
	"os"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var (
	reCheckbox = regexp.MustCompile(`^(\s*)-\s*\[([ xX])\]\s*(.*)$`)
	reTimer    = regexp.MustCompile(`\{&(\d+)h(\d+)m\}`)
	reDue      = regexp.MustCompile(`\{@([^}]+)\}`)
)

// ParseFile reads a markdown file and returns all task lines.
func ParseFile(path string) ([]TaskEntry, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var tasks []TaskEntry
	scanner := bufio.NewScanner(f)
	lineNum := 0
	for scanner.Scan() {
		lineNum++
		line := scanner.Text()
		m := reCheckbox.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		indent := calcIndentLevel(m[1])
		checked := strings.ToLower(m[2]) == "x"
		text := m[3]

		timerMins := parseTimer(text)
		due, dueRaw := parseDue(text)

		tasks = append(tasks, TaskEntry{
			FilePath:     path,
			LineNumber:   lineNum,
			Text:         text,
			Checked:      checked,
			TimerMinutes: timerMins,
			DueDate:      due,
			DueRaw:       dueRaw,
			IndentLevel:  indent,
		})
	}
	return tasks, scanner.Err()
}

// DisplayText returns task text with timer and due annotations stripped.
func DisplayText(text string) string {
	t := reTimer.ReplaceAllString(text, "")
	t = reDue.ReplaceAllString(t, "")
	return strings.TrimSpace(t)
}

func calcIndentLevel(prefix string) int {
	if strings.Contains(prefix, "\t") {
		count := 0
		for _, c := range prefix {
			if c == '\t' {
				count++
			}
		}
		return count
	}
	return len(prefix) / 2
}

func parseTimer(text string) int {
	m := reTimer.FindStringSubmatch(text)
	if m == nil {
		return 0
	}
	h, _ := strconv.Atoi(m[1])
	min, _ := strconv.Atoi(m[2])
	return h*60 + min
}

func parseDue(text string) (*time.Time, string) {
	m := reDue.FindStringSubmatch(text)
	if m == nil {
		return nil, ""
	}
	raw := m[1]
	now := time.Now()

	formats := []string{
		"1/2/2006, 15:04", "1/2/06, 15:04",
		"1/2/2006", "1/2/06",
		"1/2, 15:04", "1/2",
	}
	for _, fmt := range formats {
		t, err := time.Parse(fmt, raw)
		if err != nil {
			continue
		}
		if t.Year() == 0 {
			t = t.AddDate(now.Year(), 0, 0)
		}
		if t.Before(now.AddDate(0, -6, 0)) {
			t = t.AddDate(1, 0, 0)
		}
		return &t, raw
	}
	return nil, raw
}
