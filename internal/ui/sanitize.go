package ui

import (
	"strings"
	"unicode"
)

// StripControl removes terminal escape sequences' raw material (C0/C1 controls, DEL) and bidi overrides
// from untrusted text, so an AI-written page cannot redraw or hide what the human reviewer sees.
func StripControl(s string) string {
	return strings.Map(func(r rune) rune {
		switch {
		case r == '\n' || r == '\t':
			return r
		case unicode.IsControl(r), r >= 0x202A && r <= 0x202E, r >= 0x2066 && r <= 0x2069:
			return -1
		}
		return r
	}, s)
}
