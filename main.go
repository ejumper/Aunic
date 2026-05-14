package main

import (
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: aunic <file>")
		os.Exit(1)
	}
	filepath := os.Args[1]

	content, err := os.ReadFile(filepath)
	if err != nil {
		if os.IsNotExist(err) {
			content = []byte{}
		} else {
			fmt.Fprintf(os.Stderr, "error reading %s: %v\n", filepath, err)
			os.Exit(1)
		}
	}

	m := newApp(filepath, string(content))
	p := tea.NewProgram(m, tea.WithAltScreen(), tea.WithMouseCellMotion())

	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}
