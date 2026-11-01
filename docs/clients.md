# Connect AI assistants to lobe-server

lobe-server speaks MCP over Streamable HTTP at `https://<your-server>/mcp`. Any assistant that supports remote MCP servers can use it. The examples below use `https://lobes.example.com/mcp`. Replace it with your server's address.

There are two ways to authenticate:

- **Static token**: the client sends `Authorization: Bearer <LOBE_API_TOKEN>` with every request. Works with every client below except Claude.ai and the Claude mobile app.
- **OAuth**: the client signs you in through your identity provider. Needed for Claude.ai and the Claude mobile app. Set it up on the server first: [server.md, step 4](server.md#4-enable-oauth-for-claudeai-and-the-claude-mobile-app-optional).

Keep the token out of files you commit. Where a client supports environment variables or secret prompts, the examples use them.

| Assistant | Auth | Section |
| :--- | :--- | :--- |
| Claude Code | static token | [Claude Code](#claude-code) |
| Claude.ai, Claude Desktop, Claude mobile | OAuth | [Claude.ai](#claudeai-claude-desktop-and-the-claude-mobile-app) |
| Cursor | static token | [Cursor](#cursor) |
| VS Code with GitHub Copilot | static token | [VS Code](#vs-code-github-copilot) |
| OpenAI Codex CLI | static token | [Codex CLI](#openai-codex-cli) |
| Gemini CLI | static token | [Gemini CLI](#gemini-cli) |
| Clients that only run local (stdio) servers | static token | [stdio bridge](#clients-that-only-run-local-servers) |

## Claude Code

```bash
claude mcp add --transport http --scope user lobe-server https://lobes.example.com/mcp \
  --header "Authorization: Bearer <LOBE_API_TOKEN>"
```

`--scope user` makes the server available in every project. For a separate Claude configuration directory, prefix the command with `CLAUDE_CONFIG_DIR=<dir>`.

Claude Code is also where the generated `/<name>-*` skills run: ask *"Install the skills for the `homelab` lobe from lobe-server"* and they are saved into `~/.claude/skills/`.

## Claude.ai, Claude Desktop, and the Claude mobile app

These use OAuth. Set up OAuth on the server first ([server.md, step 4](server.md#4-enable-oauth-for-claudeai-and-the-claude-mobile-app-optional)).

1. In Claude.ai, open **Settings → Connectors → Add custom connector**.
2. Enter a name (for example `Lobes`) and the URL `https://lobes.example.com/mcp`.
3. Open **Advanced** and paste the **OAuth Client ID** and **OAuth Client Secret** of the client you created in your identity provider.
4. Leave **Request headers** empty. Claude sends its OAuth token in the `Authorization` header, so adding your static token there would clash with it.
5. Click **Connect** and sign in with your identity provider.

The connector then also appears in Claude Desktop and the Claude mobile app.

To check that requests use OAuth, look at your reverse proxy's access log: tool calls from Claude.ai and mobile arrive with the user agent `Claude-User`.

## Cursor

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

## VS Code (GitHub Copilot)

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

## OpenAI Codex CLI

Add to `~/.codex/config.toml`. Codex reads the token from the environment variable you name:

```toml
[mcp_servers.lobe-server]
url = "https://lobes.example.com/mcp"
bearer_token_env_var = "LOBE_API_TOKEN"
```

Then `export LOBE_API_TOKEN=<token>` in the shell you start Codex from.

## Gemini CLI

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

## Clients that only run local servers

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

## Lobes on the same machine (no HTTP server)

If your lobes are on the same computer as your assistant, run lobe-server over stdio directly. No Docker, port, or token is needed:

```json
{
  "mcpServers": {
    "lobe-server": {
      "command": "python3",
      "args": ["/path/to/lobe-forge/server/app.py", "--stdio"],
      "env": {
        "LOBES_DIR": "/Users/<you>/lobes",
        "LOBE_GIT_PUSH": "1"
      }
    }
  }
}
```

## Using lobes in assistants other than Claude Code

The generated `/<name>-*` skills are Claude Code skills. In other assistants, ask in plain words and the assistant calls the `lobe_*` tools itself, for example:

- *"Search my homelab lobe for how the reverse proxy is set up."* (`lobe_search`, `lobe_read_page`)
- *"Save this page to my homelab lobe: https://example.com/article"* (`lobe_add_raw`)
- *"Ingest the pending sources in my homelab lobe."* (`lobe_pending`, `lobe_write_page`, `lobe_lint`)

All tools: [reference.md](reference.md#mcp-tools).
