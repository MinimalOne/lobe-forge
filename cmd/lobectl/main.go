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

// version is set at build time: go build -ldflags "-X main.version=v2.5.3"
var version = "dev"

func printUsage() {
	fmt.Fprintf(os.Stderr, `lobectl — operator CLI for lobe-server and human verification (%s)

Usage:
  lobectl <command> [arguments...] [flags]

Verification Commands:
  lobectl verify [review] <lobe> <slug>      Read the page in a pager, then confirm on the terminal
  lobectl verify status <lobe> <slug>        Inspect page verification metadata and hashes
  lobectl verify list <lobe>                 List pages changed since human review (Check V)

Management Commands:
  lobectl list                               List all lobes on the server
  lobectl lint <lobe> [--strict]             Run audit on a remote lobe
  lobectl sync <lobe>                        Trigger git fetch/rebase/push on the server
  lobectl export <lobe> [--out <dir>]        Download Google OKF v0.2 bundle to disk
  lobectl version                            Show lobectl version

Flags (accepted anywhere on the command line):
  --url string      lobe-server URL (default: $LOBE_SERVER_URL or https://lobes.minimal.one)
  --token string    Human verification token (default: $LOBE_HUMAN_TOKEN or ~/.config/lobe/human.token)
  --by string       Human reviewer identity (default: $LOBE_USER or current user)
  --json            Output responses in raw JSON format
  --strict          lint: strict mode
  --out string      export: output directory (default: <lobe>-okf-export)
  -h, --help        Show this help message
`, version)
}

type options struct {
	url, token, by, out string
	json, strict        bool
}

// parseArgs parses flags wherever they appear; Go's flag package alone stops at the first positional.
func parseArgs(args []string) (options, []string, error) {
	var o options
	fs := flag.NewFlagSet("lobectl", flag.ContinueOnError)
	fs.Usage = printUsage
	fs.StringVar(&o.url, "url", "", "")
	fs.StringVar(&o.token, "token", "", "")
	fs.StringVar(&o.by, "by", "", "")
	fs.StringVar(&o.out, "out", "", "")
	fs.BoolVar(&o.json, "json", false, "")
	fs.BoolVar(&o.strict, "strict", false, "")

	var pos []string
	for {
		if err := fs.Parse(args); err != nil {
			return o, nil, err
		}
		if fs.NArg() == 0 {
			return o, pos, nil
		}
		pos = append(pos, fs.Arg(0))
		args = fs.Args()[1:]
	}
}

func fail(err error) {
	fmt.Fprintf(os.Stderr, "%s\n", ui.Red("Error: "+err.Error()))
	os.Exit(1)
}

func usage(line string) {
	fmt.Fprintln(os.Stderr, "Usage: "+line)
	os.Exit(1)
}

func main() {
	o, args, err := parseArgs(os.Args[1:])
	if err == flag.ErrHelp {
		os.Exit(0)
	}
	if err != nil {
		os.Exit(1) // flag package already printed the error
	}
	if len(args) == 0 {
		printUsage()
		os.Exit(0)
	}

	cmd, cmdArgs := args[0], args[1:]
	if cmd == "version" {
		fmt.Printf("lobectl %s\n", version)
		return
	}

	cfg, err := config.Resolve(o.url, o.token, o.by)
	if err != nil {
		fail(fmt.Errorf("resolving config: %w", err))
	}
	client.UserAgent = "lobectl/" + version
	cli := client.New(cfg.ServerURL, cfg.HumanToken)
	ctx := context.Background()

	switch cmd {
	case "verify":
		err = runVerify(ctx, cli, cfg, cmdArgs, o.json)
	case "list":
		err = commands.RunList(ctx, cli, o.json)
	case "lint":
		if len(cmdArgs) != 1 {
			usage("lobectl lint <lobe> [--strict]")
		}
		err = commands.RunLint(ctx, cli, cmdArgs[0], o.strict, o.json)
	case "sync":
		if len(cmdArgs) != 1 {
			usage("lobectl sync <lobe>")
		}
		err = commands.RunSync(ctx, cli, cmdArgs[0], o.json)
	case "export":
		if len(cmdArgs) != 1 {
			usage("lobectl export <lobe> [--out <dir>]")
		}
		err = commands.RunExport(ctx, cli, cmdArgs[0], o.out, o.json)
	default:
		fmt.Fprintf(os.Stderr, "Unknown command: %s\n\n", cmd)
		printUsage()
		os.Exit(1)
	}
	if err != nil {
		fail(err)
	}
}

func runVerify(ctx context.Context, cli *client.Client, cfg *config.Config, args []string, jsonOutput bool) error {
	if len(args) == 0 {
		usage("lobectl verify [review|status|list] <lobe> [slug]")
	}
	switch args[0] {
	case "status":
		if len(args) != 3 {
			usage("lobectl verify status <lobe> <slug>")
		}
		return commands.RunVerifyStatus(ctx, cli, args[1], args[2], jsonOutput)
	case "list":
		if len(args) != 2 {
			usage("lobectl verify list <lobe>")
		}
		return commands.RunVerifyList(ctx, cli, args[1], jsonOutput)
	case "review":
		args = args[1:]
	}
	// 'lobectl verify <lobe> <slug>' is shorthand for 'verify review'
	if len(args) != 2 {
		usage("lobectl verify [review] <lobe> <slug>")
	}
	return commands.RunVerifyReview(ctx, cli, cfg, args[0], args[1], jsonOutput)
}
