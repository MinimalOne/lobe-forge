package ui

import "testing"

func TestStripControl(t *testing.T) {
	in := "ok\x1b[2J\x1b]0;x\x07\rline\u202ehid\u2066\u009b\n\ttab é"
	want := "ok[2J]0;xlinehid\n\ttab é"
	if got := StripControl(in); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}
