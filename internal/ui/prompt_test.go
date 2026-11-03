package ui

import (
	"bufio"
	"strings"
	"testing"
)

func TestAskConfirmation(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"y\n", "y"},
		{"yes\n", "y"},
		{"n\n", "n"},
		{"no\n", "n"},
		{"\n", "n"},
		{"v\n", "v"},
		{"q\n", "q"},
		{"invalid\ny\n", "y"},
	}

	for _, tt := range tests {
		r := strings.NewReader(tt.input)
		ans, err := AskConfirmation(r, "prompt: ")
		if err != nil {
			t.Fatalf("unexpected error for %q: %v", tt.input, err)
		}
		if ans != tt.expected {
			t.Errorf("for input %q expected %q, got %q", tt.input, tt.expected, ans)
		}
	}
}

func TestAskConfirmationSharedReaderKeepsBufferedInput(t *testing.T) {
	r := bufio.NewReader(strings.NewReader("n\ny\n"))
	for _, want := range []string{"n", "y"} {
		if got, err := AskConfirmation(r, ""); err != nil || got != want {
			t.Fatalf("got %q, %v; want %q", got, err, want)
		}
	}
}
