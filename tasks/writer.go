package tasks

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
)

// ToggleTask sets the checkbox on the given line to checked or unchecked.
func ToggleTask(path string, lineNumber int, checked bool) error {
	lines, mtime, err := readLines(path)
	if err != nil {
		return err
	}
	idx := lineNumber - 1
	if idx < 0 || idx >= len(lines) {
		return fmt.Errorf("line %d out of range", lineNumber)
	}
	m := reCheckbox.FindStringSubmatch(lines[idx])
	if m == nil {
		return fmt.Errorf("line %d is not a task", lineNumber)
	}
	mark := " "
	if checked {
		mark = "x"
	}
	lines[idx] = m[1] + "- [" + mark + "] " + m[3]
	return atomicWrite(path, lines, mtime)
}

// DeleteTask removes the task at the given line from the file.
func DeleteTask(path string, lineNumber int) error {
	lines, mtime, err := readLines(path)
	if err != nil {
		return err
	}
	idx := lineNumber - 1
	if idx < 0 || idx >= len(lines) {
		return fmt.Errorf("line %d out of range", lineNumber)
	}
	lines = append(lines[:idx], lines[idx+1:]...)
	return atomicWrite(path, lines, mtime)
}

// AddTask appends a task line to TASKS-<listName>.md in the list's directory,
// creating the file with a header if it doesn't exist.
func AddTask(lc ListConfig, text string) error {
	dir := lc.AbsPath()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	path := lc.GenericFile()
	if _, err := os.Stat(path); os.IsNotExist(err) {
		header := "# " + lc.Title + "\n\n"
		if err := os.WriteFile(path, []byte(header), 0o644); err != nil {
			return err
		}
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintf(f, "- [ ] %s\n", text)
	return err
}

func readLines(path string) ([]string, float64, error) {
	fi, err := os.Stat(path)
	if err != nil {
		return nil, 0, err
	}
	mtime := float64(fi.ModTime().UnixNano()) / 1e9

	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()

	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}
	return lines, mtime, scanner.Err()
}

func atomicWrite(path string, lines []string, expectedMtime float64) error {
	fi, err := os.Stat(path)
	if err != nil {
		return err
	}
	currentMtime := float64(fi.ModTime().UnixNano()) / 1e9
	if absDiff(currentMtime, expectedMtime) > 0.01 {
		return fmt.Errorf("file %s was modified externally", filepath.Base(path))
	}
	dir := filepath.Dir(path)
	tmp, err := os.CreateTemp(dir, ".tasks-*.tmp")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	w := bufio.NewWriter(tmp)
	for _, line := range lines {
		w.WriteString(line + "\n") //nolint:errcheck
	}
	if err := w.Flush(); err != nil {
		tmp.Close()
		os.Remove(tmpPath) //nolint:errcheck
		return err
	}
	tmp.Close()
	return os.Rename(tmpPath, path)
}

func absDiff(a, b float64) float64 {
	if a > b {
		return a - b
	}
	return b - a
}
