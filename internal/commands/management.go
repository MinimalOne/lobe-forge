package commands

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/MinimalOne/lobe-forge/internal/client"
	"github.com/MinimalOne/lobe-forge/internal/ui"
)

// RunList lists all lobes hosted on lobe-server.
func RunList(ctx context.Context, cli *client.Client, jsonOutput bool) error {
	res, err := cli.ListLobes(ctx)
	if err != nil {
		return err
	}

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(res)
	}

	if len(res.Lobes) == 0 {
		fmt.Println("No lobes found on server.")
		return nil
	}

	fmt.Printf("%-15s %-10s %s\n", ui.Bold("LOBE"), ui.Bold("AUTOPUSH"), ui.Bold("REMOTE"))
	fmt.Printf("%-15s %-10s %s\n", strings.Repeat("-", 15), strings.Repeat("-", 10), strings.Repeat("-", 40))

	for _, l := range res.Lobes {
		autopushStr := "disabled"
		if l.Autopush {
			autopushStr = ui.Green("enabled")
		}
		remoteStr := l.Remote
		if remoteStr == "" {
			remoteStr = ui.Dim("(none)")
		}
		fmt.Printf("%-15s %-10s %s\n", ui.Cyan(l.Name), autopushStr, remoteStr)
	}

	return nil
}

// RunLint runs server-side linting on a lobe and displays the output.
func RunLint(ctx context.Context, cli *client.Client, lobe string, strict, jsonOutput bool) error {
	res, err := cli.GetLint(ctx, lobe, strict)
	if err != nil {
		return err
	}

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(res)
	}

	if res.Stdout != "" {
		fmt.Print(res.Stdout)
		if !strings.HasSuffix(res.Stdout, "\n") {
			fmt.Println()
		}
	}
	if res.Stderr != "" {
		fmt.Fprint(os.Stderr, res.Stderr)
		if !strings.HasSuffix(res.Stderr, "\n") {
			fmt.Fprintln(os.Stderr)
		}
	}

	if res.ReturnCode != 0 {
		return fmt.Errorf("lint check failed with exit code %d", res.ReturnCode)
	}
	return nil
}

// RunSync triggers remote git fetch/pull/rebase and push on lobe-server.
func RunSync(ctx context.Context, cli *client.Client, lobe string, jsonOutput bool) error {
	res, err := cli.SyncLobe(ctx, lobe)
	if err != nil {
		return err
	}

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(res)
	}

	fmt.Printf("Sync %s: %s\n", ui.Cyan(lobe), ui.Bold(res.Status))
	if res.Commit != "" {
		fmt.Printf("Commit: %s (pulled: %v, pushed: %v)\n", res.Commit, res.Pulled, res.Pushed)
	}
	if res.PushError != nil && *res.PushError != "" {
		fmt.Printf("Push Warning: %s\n", ui.Yellow(*res.PushError))
	}

	return nil
}

// RunExport downloads the OKF bundle from lobe-server and writes files to outDir.
func RunExport(ctx context.Context, cli *client.Client, lobe, outDir string, jsonOutput bool) error {
	res, err := cli.ExportOKF(ctx, lobe)
	if err != nil {
		return err
	}

	if jsonOutput {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(res)
	}

	if outDir == "" {
		outDir = fmt.Sprintf("%s-okf-export", lobe)
	}

	if err := os.MkdirAll(outDir, 0755); err != nil {
		return fmt.Errorf("create export directory %s: %w", outDir, err)
	}

	count := 0
	for _, f := range res.Files {
		target := fmt.Sprintf("%s/%s", strings.TrimRight(outDir, "/"), strings.TrimLeft(f.Path, "/"))
		if err := os.MkdirAll(target[:strings.LastIndex(target, "/")], 0755); err != nil {
			return fmt.Errorf("create dir for %s: %w", target, err)
		}
		if err := os.WriteFile(target, []byte(f.Content), 0644); err != nil {
			return fmt.Errorf("write %s: %w", target, err)
		}
		count++
	}

	fmt.Printf("%s Exported %d file(s) for lobe '%s' (OKF %s) to %s\n",
		ui.Green("✓"), count, ui.Cyan(lobe), res.OKFVersion, ui.Bold(outDir))

	return nil
}
