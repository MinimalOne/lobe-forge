package ui

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// ViewInPager displays content using the system pager ($PAGER or less -R).
// If no pager is found or stdin/stdout are not connected to a terminal, prints to stdout.
func ViewInPager(content string) error {
	pagerCmd := os.Getenv("PAGER")
	if pagerCmd == "" {
		pagerCmd = "less -R"
	}

	parts := strings.Fields(pagerCmd)
	if len(parts) == 0 {
		fmt.Println(content)
		return nil
	}

	bin, err := exec.LookPath(parts[0])
	if err != nil {
		fmt.Println(content)
		return nil
	}

	cmd := exec.Command(bin, parts[1:]...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	stdin, err := cmd.StdinPipe()
	if err != nil {
		fmt.Println(content)
		return nil
	}

	if err := cmd.Start(); err != nil {
		fmt.Println(content)
		return nil
	}

	go func() {
		defer stdin.Close()
		_, _ = io.WriteString(stdin, content)
	}()

	return cmd.Wait()
}
