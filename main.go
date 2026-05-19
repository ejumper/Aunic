package main

import (
	"bytes"
	"fmt"
	"log/slog"
	"os"
	"reflect"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/ejumper/aunic/llm"
	"github.com/ejumper/aunic/logs"
)

// kittyInputFilter translates kitty keyboard-protocol CSI-u sequences that
// bubbletea v1.x doesn't natively decode. Bubbletea v1 never requests enhanced
// keyboard mode, so terminals that use the kitty protocol (e.g. WezTerm) send
// these sequences but bubbletea emits them as the internal unknownCSISequenceMsg
// type. We intercept them here and convert to the nearest tea.KeyMsg equivalent.
func kittyInputFilter(_ tea.Model, msg tea.Msg) tea.Msg {
	rv := reflect.ValueOf(msg)
	if rv.Kind() != reflect.Slice || rv.Type().String() != "tea.unknownCSISequenceMsg" {
		return msg
	}
	b := rv.Bytes()
	switch {
	case bytes.Equal(b, []byte("\x1b[13;2u")): // shift+enter
		return tea.KeyMsg{Type: tea.KeyEnter, Alt: true}
	}
	return msg
}

func main() {
	if err := logs.Init(logs.DefaultPath()); err != nil {
		fmt.Fprintf(os.Stderr, "warning: could not open log file: %v\n", err)
	} else {
		defer logs.Close()
	}
	slog.Info("aunic start", "args", os.Args[1:])

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

	m := newApp(filepath, string(content), llm.LoadConfig())
	p := tea.NewProgram(m, tea.WithAltScreen(), tea.WithMouseCellMotion(), tea.WithFilter(kittyInputFilter))

	if _, err := p.Run(); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
}
