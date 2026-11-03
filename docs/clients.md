# Connect AI tools

Your lobes reach AI tools through the Model Context Protocol (MCP). Any tool that supports MCP can read, write, and search them:

| Kind | Tools | Section |
| :--- | :--- | :--- |
| AI coding tools | Claude Code, Codex CLI, Gemini CLI, Antigravity, Cursor, VS Code | [AI coding tools](#ai-coding-tools) |
| AI chat apps | Claude.ai, Claude Desktop, Claude mobile | [AI chat apps](#ai-chat-apps) |
| Personal AI agents | OpenClaw, Hermes Agent | [Personal AI agents](#personal-ai-agents) |
| Anything else | any client that runs local MCP servers | [Other clients](#other-clients) |

The examples connect to a self-hosted `lobe-server` at `https://lobes.example.com/mcp` ([server.md](server.md)). Replace it with your server's address. For lobes on your own computer without a server, see [Local lobes without a server](#local-lobes-without-a-server).

## Authentication

Every tool must prove it may use your lobes. There are two ways:

- **Static token**: the tool sends `Authorization: Bearer <LOBE_API_TOKEN>` with every request. Works for every tool on this page except Claude.ai and the Claude mobile app.
- **OAuth**: the tool signs you in through your identity provider. Needed for Claude.ai and the Claude mobile app. Set it up on the server first: [server.md, step 4](server.md#4-enable-oauth-for-claudeai-and-the-claude-mobile-app-optional).

Keep the token out of files you commit. Where a tool supports environment variables or secret prompts, the examples use them.

## AI coding tools

### Claude Code

```bash
claude mcp add --transport http --scope user lobe-server https://lobes.example.com/mcp \
  --header "Authorization: Bearer <LOBE_API_TOKEN>"
```

`--scope user` makes the server available in every project. For a separate Claude configuration directory, prefix the command with `CLAUDE_CONFIG_DIR=<dir>`.

Claude Code can also install slash commands for a lobe. Ask *"Install the skills for the `homelab` lobe from lobe-server"* and you get `/homelab-add-raw`, `/homelab-ingest`, `/homelab-ask`, and `/homelab-lint` in `~/.claude/skills/`. They are optional: plain requests work too.

### OpenAI Codex CLI

Add to `~/.codex/config.toml`. Codex reads the token from the environment variable you name:

```toml
[mcp_servers.lobe-server]
url = "https://lobes.example.com/mcp"
bearer_token_env_var = "LOBE_API_TOKEN"
```

Then `export LOBE_API_TOKEN=<token>` in the shell you start Codex from.

### Gemini CLI

Add to `~/.gemini/settings.json`. Use `httpUrl`, not `url`: in Gemini CLI, `url` means the older SSE transport.

```json
{
  "mcpServers": {
    "lobe-server": {
      "httpUrl": "https://lobes.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <LOBE_API_TOKEN>"
      }
    }
  }
}
```

### Antigravity

Add to `~/.gemini/config/mcp_config.json` (in the app: **Manage MCP servers → View raw config**). Antigravity uses `serverUrl` for remote servers, not `url`:

```json
{
  "mcpServers": {
    "lobe-server": {
      "serverUrl": "https://lobes.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <LOBE_API_TOKEN>"
      }
    }
  }
}
```

### Cursor

Add to `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "lobe-server": {
      "url": "https://lobes.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${env:LOBE_API_TOKEN}"
      }
    }
  }
}
```

`${env:LOBE_API_TOKEN}` reads the token from your environment, so it never sits in the file.

### VS Code (GitHub Copilot)

Add to `.vscode/mcp.json`. VS Code prompts for the token once and stores it securely:

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "lobe-token",
      "description": "lobe-server API token",
      "password": true
    }
  ],
  "servers": {
    "lobe-server": {
      "type": "http",
      "url": "https://lobes.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${input:lobe-token}"
      }
    }
  }
}
```

## AI chat apps

### Claude.ai, Claude Desktop, and the Claude mobile app

These use OAuth. Set up OAuth on the server first ([server.md, step 4](server.md#4-enable-oauth-for-claudeai-and-the-claude-mobile-app-optional)).

1. In Claude.ai, open **Settings → Connectors → Add custom connector**.
2. Enter a name (for example `Lobes`) and the URL `https://lobes.example.com/mcp`.
3. Open **Advanced** and paste the **OAuth Client ID** and **OAuth Client Secret** of the client you created in your identity provider.
4. Leave **Request headers** empty. Claude sends its OAuth token in the `Authorization` header, so adding your static token there would clash with it.
5. Click **Connect** and sign in with your identity provider.

The connector then also appears in Claude Desktop and the Claude mobile app, so you can save to and ask your lobes from your phone.

To check that requests use OAuth, look at your reverse proxy's access log: tool calls from Claude.ai and mobile arrive with the user agent `Claude-User`.

## Personal AI agents

### Hermes Agent

Add to `~/.hermes/config.yaml`, then start a new session or run `/reload-mcp`:

```yaml
mcp_servers:
  lobe-server:
    url: "https://lobes.example.com/mcp"
    headers:
      Authorization: "Bearer <LOBE_API_TOKEN>"
```

### OpenClaw

Add the server from the CLI and check that it responds:

```bash
openclaw mcp add lobe-server --url https://lobes.example.com/mcp --transport streamable-http
openclaw mcp doctor lobe-server --probe
```

This creates an entry under `mcp.servers` in your OpenClaw config. Add the `Authorization: Bearer <LOBE_API_TOKEN>` header through OpenClaw's secret mechanism rather than as a literal in the config; see the [OpenClaw MCP docs](https://docs.openclaw.ai/tools/mcp).

## Other clients

### Clients that only run local servers

Some clients can only start MCP servers as local commands (stdio). This small bridge forwards each message to lobe-server over HTTPS. Save it as `~/.local/bin/lobe-mcp-https`:

```bash
cat <<'EOF' > ~/.local/bin/lobe-mcp-https
#!/usr/bin/env python3
"""Stdio-to-HTTP MCP bridge for lobe-server."""
import os, sys, urllib.request

URL = os.environ.get("LOBE_MCP_URL", "https://lobes.example.com/mcp")
TOKEN = os.environ.get("LOBE_API_TOKEN", "")

for line in sys.stdin:
    body = line.strip()
    if not body:
        continue
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(URL, data=body.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = resp.read().decode("utf-8").strip()
        if out:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
EOF
chmod +x ~/.local/bin/lobe-mcp-https
```

Register it in the client's MCP configuration:

```json
{
  "mcpServers": {
    "lobe-server": {
      "command": "/Users/<you>/.local/bin/lobe-mcp-https",
      "env": {
        "LOBE_MCP_URL": "https://lobes.example.com/mcp",
        "LOBE_API_TOKEN": "<LOBE_API_TOKEN>"
      }
    }
  }
}
```

If you also add the Claude.ai OAuth connector, don't register this bridge in Claude Desktop too. Otherwise Desktop has two lobe servers and may use either.

## Local lobes without a server

Lobes on your own computer (for example those created by `/lobe-forge` in `~/lobes/`) can be served to any MCP client over stdio. No Docker, port, or token is needed, only a clone of this repository:

```bash
git clone https://github.com/MinimalOne/lobe-forge.git ~/lobe-forge
```

Then register this server in your tool. The JSON shape below works for Claude Desktop, Cursor, Antigravity, and Gemini CLI. For Codex, Hermes Agent, and others, use their stdio (`command` + `args`) format.

```json
{
  "mcpServers": {
    "lobes": {
      "command": "python3",
      "args": ["/Users/<you>/lobe-forge/server/app.py", "--stdio"],
      "env": {
        "LOBES_DIR": "/Users/<you>/lobes"
      }
    }
  }
}
```

Each lobe must be a folder directly inside `LOBES_DIR` (for example `~/lobes/homelab`). Lobes kept elsewhere, such as wikis adopted inside another repository, are not listed. Every change is committed in the lobe's Git repository. Add `"LOBE_GIT_PUSH": "1"` to `env` to also push each change to the lobe's remote.

## What to say to your AI

With a lobe connected, ask in plain words. The tool picks the `lobe_*` tools itself:

| You say | Tools used |
| :--- | :--- |
| *"Save this page to my homelab lobe: https://example.com/article"* | `lobe_add_raw` |
| *"Ingest the pending sources in my homelab lobe."* | `lobe_pending`, `lobe_search`, `lobe_write_page`, `lobe_edit_page`, `lobe_lint` |
| *"Ask my homelab lobe how the reverse proxy is set up."* | `lobe_search`, `lobe_read_page` |
| *"Check my homelab lobe for broken links."* | `lobe_lint` |
| *"Create a `german` lobe for my B1 grammar notes."* | `lobe_create` |

All tools: [reference.md](reference.md#mcp-tools).
