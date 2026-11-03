package ui

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// ViewInPager displays untrusted content, control characters stripped, in $PAGER (default: less).
// If the pager cannot be started, it prints to out.
func ViewInPager(content string, out *os.File) error {
	content = StripControl(content)
	pagerCmd := os.Getenv("PAGER")
	if pagerCmd == "" {
		pagerCmd = "less -F" // -F: a page that fits on one screen needs no q before the prompt
	}

	parts := strings.Fields(pagerCmd)
	if len(parts) == 0 {
		fmt.Fprintln(out, content)
		return nil
	}

	bin, err := exec.LookPath(parts[0])
	if err != nil {
		fmt.Fprintln(out, content)
		return nil
	}

	cmd := exec.Command(bin, parts[1:]...)
	cmd.Stdout = out
	cmd.Stderr = os.Stderr

	stdin, err := cmd.StdinPipe()
	if err != nil {
		fmt.Fprintln(out, content)
		return nil
	}

	if err := cmd.Start(); err != nil {
		fmt.Fprintln(out, content)
		return nil
	}

	go func() {
		defer stdin.Close()
		_, _ = io.WriteString(stdin, content)
	}()

	return cmd.Wait()
}
