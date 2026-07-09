package tasks

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// diskCache is the JSON structure persisted to the XDG data dir.
type diskCache struct {
	Files map[string]FileTaskCache `json:"files"`
}

// TaskIndex aggregates tasks across configured directories with mtime caching.
type TaskIndex struct {
	files   map[string]FileTaskCache
	changed bool
}

func cacheFilePath() string {
	base := os.Getenv("XDG_DATA_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".local", "share")
	}
	return filepath.Join(base, "aunic", "tasks-cache.json")
}

// LoadIndex loads the on-disk cache. Returns an empty index on error.
func LoadIndex() *TaskIndex {
	idx := &TaskIndex{files: map[string]FileTaskCache{}}
	data, err := os.ReadFile(cacheFilePath())
	if err != nil {
		return idx
	}
	var dc diskCache
	if err := json.Unmarshal(data, &dc); err != nil {
		return idx
	}
	if dc.Files != nil {
		idx.files = dc.Files
	}
	return idx
}

// Refresh walks each configured list directory, re-parses .md files whose
// mtime is newer than the cache entry, and evicts entries for deleted files.
func (idx *TaskIndex) Refresh(lists []ListConfig) {
	seen := map[string]bool{}
	for _, lc := range lists {
		absPath := lc.AbsPath()
		_ = filepath.Walk(absPath, func(path string, info os.FileInfo, err error) error {
			if err != nil || info.IsDir() || filepath.Ext(path) != ".md" {
				return nil
			}
			seen[path] = true
			mtime := float64(info.ModTime().UnixNano()) / 1e9
			if cached, ok := idx.files[path]; ok && absDiff(cached.Mtime, mtime) < 0.01 {
				return nil
			}
			tasks, err := ParseFile(path)
			if err != nil {
				return nil
			}
			idx.files[path] = FileTaskCache{Mtime: mtime, Tasks: tasks}
			idx.changed = true
			return nil
		})
	}
	for path := range idx.files {
		if !seen[path] {
			delete(idx.files, path)
			idx.changed = true
		}
	}
}

// Save writes the current index to disk if anything changed since last load.
func (idx *TaskIndex) Save() {
	if !idx.changed {
		return
	}
	cp := cacheFilePath()
	if err := os.MkdirAll(filepath.Dir(cp), 0o755); err != nil {
		return
	}
	data, err := json.Marshal(diskCache{Files: idx.files})
	if err != nil {
		return
	}
	_ = os.WriteFile(cp, data, 0o644)
	idx.changed = false
}

// TasksForList returns all tasks from files within the given list's path
// (including subdirectories). Files sorted newest-first; within each file,
// tasks are in file order.
func (idx *TaskIndex) TasksForList(lc ListConfig) []TaskEntry {
	absPath := lc.AbsPath()

	type fileEntry struct {
		mtime float64
		tasks []TaskEntry
	}
	var files []fileEntry
	for path, fc := range idx.files {
		if isUnder(filepath.Dir(path), absPath) {
			files = append(files, fileEntry{fc.Mtime, fc.Tasks})
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mtime > files[j].mtime })

	var out []TaskEntry
	for _, fe := range files {
		out = append(out, fe.tasks...)
	}
	return out
}

// ListsSortedByMtime returns lists sorted by the most recent file mtime within
// them (most recently modified first).
func (idx *TaskIndex) ListsSortedByMtime(lists []ListConfig) []ListConfig {
	type scored struct {
		lc    ListConfig
		mtime float64
	}
	var entries []scored
	for _, lc := range lists {
		absPath := lc.AbsPath()
		var maxMtime float64
		for path, fc := range idx.files {
			if isUnder(filepath.Dir(path), absPath) && fc.Mtime > maxMtime {
				maxMtime = fc.Mtime
			}
		}
		entries = append(entries, scored{lc, maxMtime})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].mtime > entries[j].mtime })
	out := make([]ListConfig, len(entries))
	for i, e := range entries {
		out[i] = e.lc
	}
	return out
}

// isUnder returns true when dir is absPath or a subdirectory of it.
func isUnder(dir, absPath string) bool {
	dir = filepath.Clean(dir) + string(filepath.Separator)
	absPath = filepath.Clean(absPath) + string(filepath.Separator)
	return strings.HasPrefix(dir, absPath)
}
