package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/ejumper/aunic/tasks"
)

// cmdTasks implements the "aunic tasks" subcommand family:
//
//	aunic tasks lists                     — list all configured task lists
//	aunic tasks list [--list <name>]      — show tasks in a list (auto-detect from cwd)
//	aunic tasks add "text" [--list <name>] — add task to TASKS-<name>.md
func cmdTasks(args []string) int {
	lists := tasks.LoadListConfigs()
	if len(lists) == 0 {
		fmt.Fprintln(os.Stderr, "no task_lists configured in aunic.json")
		return 1
	}

	if len(args) == 0 {
		printTasksUsage()
		return 1
	}

	idx := tasks.LoadIndex()
	idx.Refresh(lists)
	defer idx.Save()

	switch args[0] {
	case "lists":
		return cmdTasksLists(lists, idx)
	case "list":
		return cmdTasksList(args[1:], lists, idx)
	case "add":
		return cmdTasksAdd(args[1:], lists, idx)
	default:
		fmt.Fprintf(os.Stderr, "unknown tasks subcommand %q\n", args[0])
		printTasksUsage()
		return 1
	}
}

func cmdTasksLists(lists []tasks.ListConfig, idx *tasks.TaskIndex) int {
	sorted := idx.ListsSortedByMtime(lists)
	for _, lc := range sorted {
		all := idx.TasksForList(lc)
		total, done := 0, 0
		for _, t := range all {
			total++
			if t.Checked {
				done++
			}
		}
		fmt.Printf("%-20s  %s  (%d/%d done)\n", lc.Name, lc.Title, done, total)
	}
	return 0
}

func cmdTasksList(args []string, lists []tasks.ListConfig, idx *tasks.TaskIndex) int {
	lc, ok := resolveList(args, lists)
	if !ok {
		return 1
	}
	entries := idx.TasksForList(lc)
	if len(entries) == 0 {
		fmt.Printf("No tasks in list %q\n", lc.Title)
		return 0
	}
	fmt.Printf("━━━ %s ━━━\n", lc.Title)
	for _, t := range entries {
		mark := " "
		if t.Checked {
			mark = "x"
		}
		indent := strings.Repeat("  ", t.IndentLevel)
		fmt.Printf("%s- [%s] %s\n", indent, mark, tasks.DisplayText(t.Text))
	}
	return 0
}

func cmdTasksAdd(args []string, lists []tasks.ListConfig, idx *tasks.TaskIndex) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: aunic tasks add \"task text\" [--list <name>]")
		return 1
	}
	// The text is the first non-flag argument.
	text := ""
	listFlag := ""
	for i := 0; i < len(args); i++ {
		if args[i] == "--list" && i+1 < len(args) {
			listFlag = args[i+1]
			i++
		} else if !strings.HasPrefix(args[i], "--") {
			text = args[i]
		}
	}
	if text == "" {
		fmt.Fprintln(os.Stderr, "task text is required")
		return 1
	}

	var lc *tasks.ListConfig
	if listFlag != "" {
		for i := range lists {
			if lists[i].Name == listFlag {
				cp := lists[i]
				lc = &cp
				break
			}
		}
		if lc == nil {
			fmt.Fprintf(os.Stderr, "list %q not found in aunic.json\n", listFlag)
			return 1
		}
	} else {
		cwd, _ := os.Getwd()
		lc = tasks.BestListForDir(cwd, lists)
		if lc == nil {
			// Fall back to the first list
			cp := lists[0]
			lc = &cp
		}
	}

	if err := tasks.AddTask(*lc, text); err != nil {
		fmt.Fprintf(os.Stderr, "error adding task: %v\n", err)
		return 1
	}
	fmt.Printf("added to %s (%s)\n", lc.Title, filepath.Base(lc.GenericFile()))
	return 0
}

// resolveList finds a ListConfig from --list flag or auto-detects from cwd.
func resolveList(args []string, lists []tasks.ListConfig) (tasks.ListConfig, bool) {
	for i := 0; i < len(args); i++ {
		if args[i] == "--list" && i+1 < len(args) {
			name := args[i+1]
			for _, lc := range lists {
				if lc.Name == name {
					return lc, true
				}
			}
			fmt.Fprintf(os.Stderr, "list %q not found\n", name)
			return tasks.ListConfig{}, false
		}
	}
	cwd, _ := os.Getwd()
	best := tasks.BestListForDir(cwd, lists)
	if best != nil {
		return *best, true
	}
	// Default to showing all lists together under the first one
	if len(lists) > 0 {
		return lists[0], true
	}
	fmt.Fprintln(os.Stderr, "no task list found for current directory")
	return tasks.ListConfig{}, false
}

func printTasksUsage() {
	fmt.Fprintln(os.Stderr, "usage:")
	fmt.Fprintln(os.Stderr, "  aunic tasks lists                     list all configured task lists")
	fmt.Fprintln(os.Stderr, "  aunic tasks list [--list <name>]      show tasks in a list")
	fmt.Fprintln(os.Stderr, "  aunic tasks add \"text\" [--list <name>] add a task")
}
