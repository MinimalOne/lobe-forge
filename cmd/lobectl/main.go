package main

import (
	"context"
	"flag"
	"fmt"
	"os"

	"github.com/MinimalOne/lobe-forge/internal/client"
	"github.com/MinimalOne/lobe-forge/internal/commands"
	"github.com/MinimalOne/lobe-forge/internal/config"
	"github.com/MinimalOne/lobe-forge/internal/ui"
)

const version = "1.0.0"

func printUsage() {
	fmt.Printf(`lobectl — operator CLI for lobe-server and human verification (v%s)

Usage:
  lobectl [global flags] <command> [arguments...]

Verification Commands:
  lobectl verify [review] <lobe> <slug>      Interactive review and verification with pager
  lobectl verify status <lobe> <slug>        Inspect page verification metadata and hashes
  lobectl verify list <lobe>                 List pages changed since human review (Check V)
  lobectl verify stamp <lobe> <slug...>      Directly stamp page verification (--yes to skip confirm)

Management Commands:
  lobectl list                               List all lobes on the server
  lobectl lint <lobe> [--strict]             Run audit on a remote lobe
  lobectl sync <lobe>                        Trigger git fetch/rebase/push on the server
  lobectl export <lobe> [--out <dir>]        Download Google OKF v0.2 bundle to disk
  lobectl version                            Show lobectl version

Global Flags:
  --url string      lobe-server URL (default: $LOBE_SERVER_URL or https://lobes.minimal.one)
  --token string    Human verification token (default: $LOBE_HUMAN_TOKEN or ~/.config/lobe/human.token)
  --by string       Human reviewer identity (default: $LOBE_USER or current user)
  --json            Output responses in raw JSON format
  -h, --help        Show this help message
`, version)
}

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(0)
	}

	// Parse global flags
	fs := flag.NewFlagSet("lobectl", flag.ExitOnError)
	var (
		flagURL      string
		flagToken    string
		flagReviewer string
		flagJSON     bool
		flagHelp     bool
	)

	fs.StringVar(&flagURL, "url", "", "lobe-server base URL")
	fs.StringVar(&flagToken, "token", "", "Human verification token")
	fs.StringVar(&flagReviewer, "by", "", "Human reviewer identity")
	fs.BoolVar(&flagJSON, "json", false, "Output raw JSON")
	fs.BoolVar(&flagHelp, "help", false, "Show help")
	fs.BoolVar(&flagHelp, "h", false, "Show help")

	// Parse flags that appear before subcommand
	if err := fs.Parse(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}

	args := fs.Args()
	if flagHelp || len(args) == 0 {
		printUsage()
		os.Exit(0)
	}

	cmd := args[0]
	cmdArgs := args[1:]

	if cmd == "version" {
		fmt.Printf("lobectl v%s\n", version)
		os.Exit(0)
	}

	// Resolve configuration
	cfg, err := config.Resolve(flagURL, flagToken, flagReviewer)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error resolving config: %v\n", err)
		os.Exit(1)
	}

	cli := client.New(cfg.ServerURL, cfg.HumanToken)
	ctx := context.Background()

	switch cmd {
	case "verify":
		handleVerify(ctx, cli, cfg, cmdArgs, flagJSON)
	case "list":
		if err := commands.RunList(ctx, cli, flagJSON); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
	case "lint":
		handleLint(ctx, cli, cmdArgs, flagJSON)
	case "sync":
		if len(cmdArgs) < 1 {
			fmt.Fprintln(os.Stderr, "Usage: lobectl sync <lobe>")
			os.Exit(1)
		}
		if err := commands.RunSync(ctx, cli, cmdArgs[0], flagJSON); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
	case "export":
		handleExport(ctx, cli, cmdArgs, flagJSON)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n\n", cmd)
		printUsage()
		os.Exit(1)
	}
}

func handleVerify(ctx context.Context, cli *client.Client, cfg *config.Config, args []string, jsonOutput bool) {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "Usage: lobectl verify <subcommand|lobe> [args...]")
		fmt.Fprintln(os.Stderr, "Subcommands: review (default), status, list, stamp")
		os.Exit(1)
	}

	sub := args[0]
	switch sub {
	case "status":
		if len(args) < 3 {
			fmt.Fprintln(os.Stderr, "Usage: lobectl verify status <lobe> <slug>")
			os.Exit(1)
		}
		if err := commands.RunVerifyStatus(ctx, cli, args[1], args[2], jsonOutput); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
	case "list":
		if len(args) < 2 {
			fmt.Fprintln(os.Stderr, "Usage: lobectl verify list <lobe>")
			os.Exit(1)
		}
		if err := commands.RunVerifyList(ctx, cli, args[1], jsonOutput); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
	case "stamp":
		fs := flag.NewFlagSet("stamp", flag.ExitOnError)
		yes := fs.Bool("yes", false, "Skip confirmation")
		_ = fs.Parse(args[1:])
		stampArgs := fs.Args()
		if len(stampArgs) < 2 {
			fmt.Fprintln(os.Stderr, "Usage: lobectl verify stamp <lobe> <slug...> [--yes]")
			os.Exit(1)
		}
		if err := commands.RunVerifyStamp(ctx, cli, cfg, stampArgs[0], stampArgs[1:], *yes, jsonOutput); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
	case "review":
		if len(args) < 3 {
			fmt.Fprintln(os.Stderr, "Usage: lobectl verify review <lobe> <slug>")
			os.Exit(1)
		}
		if err := commands.RunVerifyReview(ctx, cli, cfg, args[1], args[2], jsonOutput); err != nil {
			fmt.Fprintf(os.Stderr, "Error: %v\n", err)
			os.Exit(1)
		}
	default:
		// Default shortcut: 'lobectl verify <lobe> <slug>' -> interactive review
		if len(args) >= 2 {
			if err := commands.RunVerifyReview(ctx, cli, cfg, args[0], args[1], jsonOutput); err != nil {
				fmt.Fprintf(os.Stderr, "Error: %v\n", err)
				os.Exit(1)
			}
			return
		}
		fmt.Fprintf(os.Stderr, "Unknown verify subcommand or missing argument: %s\n", sub)
		os.Exit(1)
	}
}

func handleLint(ctx context.Context, cli *client.Client, args []string, jsonOutput bool) {
	fs := flag.NewFlagSet("lint", flag.ExitOnError)
	strict := fs.Bool("strict", false, "Strict lint mode")
	_ = fs.Parse(args)
	lintArgs := fs.Args()
	if len(lintArgs) < 1 {
		fmt.Fprintln(os.Stderr, "Usage: lobectl lint <lobe> [--strict]")
		os.Exit(1)
	}
	if err := commands.RunLint(ctx, cli, lintArgs[0], *strict, jsonOutput); err != nil {
		fmt.Fprintf(os.Stderr, "%s\n", ui.Red(err.Error()))
		os.Exit(1)
	}
}

func handleExport(ctx context.Context, cli *client.Client, args []string, jsonOutput bool) {
	fs := flag.NewFlagSet("export", flag.ExitOnError)
	outDir := fs.String("out", "", "Output directory")
	_ = fs.Parse(args)
	exportArgs := fs.Args()
	if len(exportArgs) < 1 {
		fmt.Fprintln(os.Stderr, "Usage: lobectl export <lobe> [--out <dir>]")
		os.Exit(1)
	}
	if err := commands.RunExport(ctx, cli, exportArgs[0], *outDir, jsonOutput); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}
