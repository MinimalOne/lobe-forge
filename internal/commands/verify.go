package commands

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"

	"github.com/MinimalOne/lobe-forge/internal/client"
	"github.com/MinimalOne/lobe-forge/internal/config"
	"github.com/MinimalOne/lobe-forge/internal/ui"
)

// RunVerifyStatus prints the verification status of a single page.
func RunVerifyStatus(ctx context.Context, cli *client.Client, lobe, slug string, jsonOutput bool) error {
	st, err := cli.GetVerifyStatus(ctx, lobe, slug)
	if err != nil {
		return err
	}

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(st)
	}

	fmt.Printf("%s: %s / %s\n", ui.Bold("Page"), ui.Cyan(lobe), ui.Cyan(slug))
	fmt.Printf("%s: ", ui.Bold("Status"))
	if !st.IsVerified {
		fmt.Printf("%s\n", ui.BadgeUnverified())
	} else if st.IsStale {
		fmt.Printf("%s (content changed since review)\n", ui.BadgeStale())
	} else {
		fmt.Printf("%s\n", ui.BadgeVerified())
	}

	fmt.Printf("%s: %s\n", ui.Bold("Current Hash"), st.Hash)
	if st.Verified != nil {
		fmt.Printf("%s: %s\n", ui.Bold("Reviewed Hash"), st.Verified.Sha)
		fmt.Printf("%s: %s\n", ui.Bold("Reviewer"), st.Verified.By)
		fmt.Printf("%s: %s\n", ui.Bold("Reviewed At"), st.Verified.At)
	}

	return nil
}

// RunVerifyReview performs an interactive review with a pager and confirmation prompt.
func RunVerifyReview(ctx context.Context, cli *client.Client, cfg *config.Config, lobe, slug string, jsonOutput bool) error {
	st, err := cli.GetVerifyStatus(ctx, lobe, slug)
	if err != nil {
		return err
	}

	page, err := cli.GetPage(ctx, lobe, slug)
	if err != nil {
		return err
	}

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(map[string]any{
			"status": st,
			"page":   page,
		})
	}

	// The confirmation is read from the controlling terminal, never stdin, so `yes | lobectl ...` cannot answer it.
	// shortcut: anything that can drive a pty as this user (expect, script) still can; only a credential kept off
	// this machine (the browser verify page, per-reviewer OAuth) keeps agents out entirely.
	tty, err := os.Open("/dev/tty")
	if err != nil {
		return fmt.Errorf("verification needs an interactive terminal: %w", err)
	}
	defer tty.Close()
	answers := bufio.NewReader(tty)

	for {
		fmt.Println()
		fmt.Printf("=== Reviewing %s / %s ===\n", ui.Cyan(lobe), ui.Cyan(slug))
		if !st.IsVerified {
			fmt.Printf("Status: %s\n", ui.BadgeUnverified())
		} else if st.IsStale {
			fmt.Printf("Status: %s (reviewed sha: %s != current: %s)\n", ui.BadgeStale(), st.Verified.Sha, st.Hash)
		} else {
			fmt.Printf("Status: %s (reviewed by %s at %s)\n", ui.BadgeVerified(), st.Verified.By, st.Verified.At)
		}
		fmt.Printf("Content Hash: %s (%d lines)\n\n", ui.Bold(st.Hash), page.Lines)

		if err := ui.ViewInPager(page.Content); err != nil {
			fmt.Fprintf(os.Stderr, "pager warning: %v\n", err)
		}

		prompt := fmt.Sprintf("\nVerify %s/%s (hash: %s) as human:%s? [y/N/v(iew)/q(uit)]: ",
			lobe, slug, st.Hash, cfg.Reviewer)

		choice, err := ui.AskConfirmation(answers, prompt)
		if err != nil {
			return err
		}

		switch choice {
		case "y":
			res, err := cli.VerifyPage(ctx, lobe, slug, st.Hash, cfg.Reviewer)
			if err != nil {
				return fmt.Errorf("verification failed: %w", err)
			}
			fmt.Printf("%s Verified %s/%s (sha: %s, commit: %s, pushed: %v)\n",
				ui.BadgeVerified(), lobe, slug, res.Verified.Sha, res.Commit, res.Pushed)
			return nil
		case "v":
			continue
		case "n":
			fmt.Println("Skipped.")
			return nil
		case "q":
			fmt.Println("Aborted.")
			return nil
		}
	}
}

// StaleVerificationItem represents a page flagged as stale or unverified.
type StaleVerificationItem struct {
	Slug       string `json:"slug"`
	Issue      string `json:"issue"`
	Details    string `json:"details"`
	OldSha     string `json:"old_sha,omitempty"`
	CurrentSha string `json:"current_sha,omitempty"`
	ReviewedBy string `json:"reviewed_by,omitempty"`
}

var staleRegex = regexp.MustCompile(`(?i)^\s*\d+\.\s+([A-Za-z0-9_-]+)\.md\s+—\s+content changed since review by ([^ ]+) on ([^ ]+) \(sha ([a-f0-9]+) != ([a-f0-9]+)\)`)

// parseStaleV pulls the Check V items out of `wikictl lint` output.
// shortcut: parses human-readable text; a structured server endpoint is the upgrade if the lint wording changes.
func parseStaleV(stdout string) []StaleVerificationItem {
	var items []StaleVerificationItem
	inCheckV := false
	for _, line := range strings.Split(stdout, "\n") {
		if strings.HasPrefix(line, "V. Stale verification") {
			inCheckV = true
			continue
		}
		if inCheckV && len(line) > 0 && line[0] >= 'A' && line[0] <= 'Z' {
			inCheckV = false
		}
		if !inCheckV {
			continue
		}
		if m := staleRegex.FindStringSubmatch(line); len(m) == 6 {
			items = append(items, StaleVerificationItem{
				Slug:       m[1],
				Issue:      "STALE (changed since review)",
				ReviewedBy: m[2],
				OldSha:     m[4],
				CurrentSha: m[5],
				Details:    fmt.Sprintf("reviewed by %s on %s", m[2], m[3]),
			})
		}
	}
	return items
}

// RunVerifyList reports pages whose content changed since a human verified them (Lint Check V).
func RunVerifyList(ctx context.Context, cli *client.Client, lobe string, jsonOutput bool) error {
	lint, err := cli.GetLint(ctx, lobe, true, true)
	if err != nil {
		return fmt.Errorf("lint %s: %w", lobe, err)
	}
	items := parseStaleV(lint.Stdout)

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(items)
	}

	if len(items) == 0 {
		fmt.Printf("%s No stale-verified pages found in lobe '%s'.\n", ui.Green("✓"), ui.Cyan(lobe))
		return nil
	}

	fmt.Printf("%s found %d page(s) changed since human review in lobe '%s':\n\n",
		ui.BadgeStale(), len(items), ui.Cyan(lobe))
	fmt.Printf("%-30s %-25s %s\n", ui.Bold("SLUG"), ui.Bold("STATUS"), ui.Bold("DETAILS"))
	fmt.Printf("%-30s %-25s %s\n", strings.Repeat("-", 30), strings.Repeat("-", 25), strings.Repeat("-", 35))

	for _, item := range items {
		fmt.Printf("%-30s %-25s %s\n",
			ui.Cyan(item.Slug),
			ui.BadgeStale(),
			item.Details)
	}
	fmt.Println()
	fmt.Printf("Run 'lobectl verify %s <slug>' to review and verify changes.\n", lobe)

	return nil
}
