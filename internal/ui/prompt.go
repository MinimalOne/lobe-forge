package ui

import (
	"bufio"
	"fmt"
	"io"
	"strings"
)

// AskConfirmation prompts the user for verification action.
// Returns "y" (yes), "n" (no), "v" (view), "q" (quit).
func AskConfirmation(r io.Reader, prompt string) (string, error) {
	reader := bufio.NewReader(r)
	for {
		fmt.Print(prompt)
		line, err := reader.ReadString('\n')
		if err != nil {
			return "", err
		}
		ans := strings.ToLower(strings.TrimSpace(line))
		switch ans {
		case "y", "yes":
			return "y", nil
		case "n", "no":
			return "n", nil
		case "v", "view":
			return "v", nil
		case "q", "quit":
			return "q", nil
		case "":
			return "n", nil
		default:
			fmt.Println("Please enter 'y' to verify, 'n' to skip, 'v' to view content, or 'q' to quit.")
		}
	}
}
