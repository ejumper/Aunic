package tasks

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// ListConfig is one entry from the "task_lists" key in aunic.json.
type ListConfig struct {
	Name  string `json:"name"`
	Path  string `json:"path"`
	Title string `json:"title"`
}

// AbsPath returns the expanded absolute path for the list.
func (lc ListConfig) AbsPath() string {
	p := lc.Path
	if strings.HasPrefix(p, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			p = filepath.Join(home, p[2:])
		}
	}
	return filepath.Clean(p)
}

// GenericFile returns the path to the auto-created TASKS-<name>.md for this list.
func (lc ListConfig) GenericFile() string {
	return filepath.Join(lc.AbsPath(), "TASKS-"+lc.Name+".md")
}

// LoadListConfigs reads aunic.json and returns the task_lists array.
// Returns nil if the file is missing, unreadable, or has no task_lists.
func LoadListConfigs() []ListConfig {
	data, err := os.ReadFile(aunicConfigPath())
	if err != nil {
		return nil
	}
	var fc struct {
		TaskLists []ListConfig `json:"task_lists"`
	}
	if err := json.Unmarshal(data, &fc); err != nil {
		return nil
	}
	return fc.TaskLists
}

// BestListForDir returns the most specific configured list whose path is a
// prefix of dir. Returns nil if no list matches.
func BestListForDir(dir string, lists []ListConfig) *ListConfig {
	var best *ListConfig
	bestLen := 0
	dir = filepath.Clean(dir) + string(filepath.Separator)
	for i := range lists {
		lp := lists[i].AbsPath() + string(filepath.Separator)
		if strings.HasPrefix(dir, lp) && len(lp) > bestLen {
			cp := lists[i]
			best = &cp
			bestLen = len(lp)
		}
	}
	return best
}

// aunicConfigPath returns the path to aunic.json, honouring XDG_CONFIG_HOME.
func aunicConfigPath() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "aunic", "aunic.json")
}
