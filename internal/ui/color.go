package ui

import (
	"fmt"
	"os"
)

var (
	colorEnabled = true
)

func init() {
	if os.Getenv("NO_COLOR") != "" || os.Getenv("TERM") == "dumb" {
		colorEnabled = false
	}
}

// SetColorEnabled manually overrides color output.
func SetColorEnabled(enabled bool) {
	colorEnabled = enabled
}

func colorize(code, text string) string {
	if !colorEnabled {
		return text
	}
	return fmt.Sprintf("\033[%sm%s\033[0m", code, text)
}

func Bold(text string) string   { return colorize("1", text) }
func Dim(text string) string    { return colorize("2", text) }
func Green(text string) string  { return colorize("32", text) }
func Red(text string) string    { return colorize("31", text) }
func Yellow(text string) string { return colorize("33", text) }
func Cyan(text string) string   { return colorize("36", text) }

// BadgeVerified returns a styled [VERIFIED] tag.
func BadgeVerified() string {
	return Green(Bold("[VERIFIED]"))
}

// BadgeStale returns a styled [STALE] tag.
func BadgeStale() string {
	return Red(Bold("[STALE]"))
}

// BadgeUnverified returns a styled [UNVERIFIED] tag.
func BadgeUnverified() string {
	return Yellow(Bold("[UNVERIFIED]"))
}
