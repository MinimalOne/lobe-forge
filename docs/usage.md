# Usage Reference

The `/lobe-forge` command runs these commands automatically. Run `scripts/forge.py` directly only for automation or troubleshooting. The `forge.py` script is in the `scripts/` folder of this repository or in `~/.claude/plugins/cache/lobe-forge/lobe-forge/<version>/`.

## Lobe Repository Layout

```text
raw/            Source files (append-only)
wiki/           Markdown pages, index.md, and log.md
wiki_tool.py    Search, links, sections, index-add, set-atoms, pending, lint, build, convert-legacy
wikictl         Wrapper script that runs wiki_tool.py on the lobe directories
LOBE.md         Page format and operational rules
skills/         <name>-add-raw, <name>-ingest, <name>-ask, <name>-lint
```

Each skill is a symbolic link in the Claude configuration directory. The skill resolves its symbolic link to find its lobe repository. Every ingestion creates a Git commit.

## Adopting Existing Wikis

The `/lobe-forge` command scans a repository or a directory of repositories for `llmwiki/` or `wiki/` directories that contain an `index.md` file. You can adopt each directory as a named lobe. The tool keeps the existing directory structure and does not rewrite existing pages.

- **In-place adoption** (for repositories that you own): The tool writes the lobe files into the repository and commits them.
- **External adoption** (for third-party or organization repositories): The tool writes the lobe files into `~/lobes/_adopted/<name>/` and creates a `repo` symbolic link to the target repository. The tool does not modify the target repository. Generated skills ask for confirmation before every commit and never push to a remote repository.

```bash
python3 scripts/forge.py scan ~/code      # Read-only scan report
```

The scan report shows:
1. Whether Git tracks the wiki directory.
2. How many authors commit to the raw source directory.
3. Which installed skills or plugins already use the wiki.

## Installing on Another Machine

Clone the lobe repository, then run:

```bash
python3 scripts/forge.py install --repo <lobe-repo> --target ~/.claude
```

For an external lobe, run `create` again with the same `<name>`, `--repo`, `--wiki-dir`, `--raw-dir`, and `--home` arguments to regenerate the external home directory.

## Upgrading Lobes

The `create` command does not overwrite existing files. After you update `lobe-forge`, run `upgrade` to update a lobe's generated files (`wiki_tool.py`, `wikictl`, `LOBE.md`, and `skills/<name>-{add-raw,ingest,ask,lint}/SKILL.md`) from the current templates:

```bash
python3 scripts/forge.py upgrade --repo <lobe-repo-or-home>           # Dry run (shows changes)
python3 scripts/forge.py upgrade --repo <lobe-repo-or-home> --apply   # Apply changes
python3 scripts/forge.py upgrade --all [--apply]                      # Upgrade all installed lobes
```

The `--all` flag finds lobes from the symbolic links in your Claude configuration directories. The `upgrade` command does not modify custom skills, manually edited skills that do not call `wikictl`, wiki pages, or raw source files.

For in-place lobes, commit all pending changes before you run `upgrade --apply`. For external lobes, `upgrade --apply` creates `*.bak` backup files.

## Uninstalling or Moving a Lobe

```bash
python3 scripts/forge.py uninstall --repo <lobe>              # Remove skill symbolic links
python3 scripts/forge.py uninstall --repo <home> --purge      # External lobe: also delete the home directory
```

To move an external lobe into a standalone repository:
1. Copy or move the wiki directory into the new repository.
2. Run `uninstall --purge` on the old external home directory.
3. Run `create` in the new repository with `--origin "<source-description>"`.

## Origin and Publishing

The `LOBE.md` file records the **Origin** of the lobe (`new`, the adopted repository remote URL, or the `--origin` value). The `/lobe-forge` skill offers to publish a lobe to your personal GitHub account only when you own the content. Published repositories are private by default.

---

## Self-Hosted Remote Server (`lobe-server`)

The `server/app.py` module runs a self-hosted HTTP REST API and Model Context Protocol (MCP) server. It uses `wiki_tool.py` for search, backlinks, section listing, index management, and linting.

### Key Features

- **Local-First Git Commits**: Every write operation (`add_raw`, `write_page`) commits changes to the local Git repository in `LOBES_DIR/<lobe>`.
- **Optional Remote Push**: The server pushes commits to `origin` only when an `origin` remote URL is configured and auto-push (`lobe.autopush=true` or `LOBE_GIT_PUSH=1`) is enabled.
- **Clone and Sync**: You can clone an existing remote Git repository when you create a lobe, or run `sync` to fetch, rebase (`git pull --rebase`), and push changes across multiple installations.
- **Semantic Release Container Images**: GitHub Actions publishes multi-architecture (`linux/amd64`, `linux/arm64`) Docker images to `ghcr.io/minimalone/lobe-forge` on every semantic release. Available tags:
  - `ghcr.io/minimalone/lobe-forge:<X.Y.Z>` (for example, `1.9.0`)
  - `ghcr.io/minimalone/lobe-forge:v<X.Y.Z>` (for example, `v1.9.0`)
  - `ghcr.io/minimalone/lobe-forge:<X.Y>` (for example, `1.9`)
  - `ghcr.io/minimalone/lobe-forge:latest`

### Running with Docker Compose

Create an environment file (for example, `/etc/lobes.env`):

```dotenv
LOBE_API_TOKEN=replace-with-a-32-byte-hex-secret
LOBE_GIT_TOKEN=ghp_replace_with_your_git_personal_access_token
LOBE_GIT_PUSH=1
```

Start the container with Docker Compose:

```yaml
services:
  lobe-server:
    image: ghcr.io/minimalone/lobe-forge:latest # Or pin to a semantic version: ghcr.io/minimalone/lobe-forge:1.9.0
    container_name: lobe-server
    restart: unless-stopped
    env_file:
      - /etc/lobes.env
    environment:
      LOBES_DIR: /data/lobes
      LOBE_HOST: 0.0.0.0
      LOBE_PORT: "8080"
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - /srv/lobes:/data/lobes
```

```bash
# Start lobe-server
docker compose -f server/docker-compose.yml up -d

# Optional: start SilverBullet web UI (port 3000) or Forgejo Git server (port 3001)
docker compose -f server/docker-compose.yml --profile webui up -d
docker compose -f server/docker-compose.yml --profile forgejo up -d
```

### Running Directly with Python

`server/app.py` uses only the Python 3.10+ standard library and requires no external pip packages:

```bash
# Start the HTTP REST and Streamable HTTP MCP server
LOBES_DIR=~/lobes LOBE_API_TOKEN=secret python3 server/app.py --host 127.0.0.1 --port 8080

# Start the MCP server over standard input/output (stdio)
LOBES_DIR=~/lobes python3 server/app.py --stdio
```

---

## Environment Variables

Configure `lobe-server` with the following environment variables:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `LOBES_DIR` | `/data/lobes` | Directory path that stores all lobe Git repositories. |
| `LOBE_HOST` | `0.0.0.0` | Bind address for the HTTP server (can be overridden by `--host`). |
| `LOBE_PORT` | `8080` | TCP port for the HTTP server (can be overridden by `--port`). |
| `LOBE_API_TOKEN` | *(unset)* | Bearer token for `/api/*` and `/mcp` HTTP requests. If unset, authentication is disabled. `/health` is always public. |
| `LOBE_GIT_PUSH` | `0` | Global auto-push fallback (`1`, `true`, or `yes`). Used when a lobe does not set `git config lobe.autopush`. |
| `LOBE_GIT_TOKEN` | *(unset)* | Personal access token (PAT) or `username:token` credential for HTTPS Git operations (`clone`, `fetch`, `pull --rebase`, `push`, `ls-remote`). |
| `LOBE_SERVER_VERSION` | *(from `plugin.json`)* | Overrides the semantic version string reported by MCP `initialize`. |

---

## Git Configuration and Authentication

### How `LOBE_GIT_TOKEN` Works Across Git Providers

When `LOBE_GIT_TOKEN` is set, `lobe-server` injects an `http.extraHeader=Authorization: Basic <base64>` flag into Git commands and sets `GIT_TERMINAL_PROMPT=0`. It does not store the token in `.git/config`.

`lobe-server` selects the HTTP Basic username prefix automatically from the remote URL:

| Git Provider | `LOBE_GIT_TOKEN` Format | Basic Auth Prefix Used |
| :--- | :--- | :--- |
| **GitHub** (`github.com`) | `<token>` (classic `ghp_...` or fine-grained `github_pat_...` with `Contents: Read and write`) | `x-access-token:<token>` |
| **Codeberg / Forgejo / Gitea** | `<token>` | `x-access-token:<token>` |
| **GitLab** (`gitlab.com` or self-hosted) | `<token>` (or `oauth2:<token>`) | `oauth2:<token>` |
| **Bitbucket Cloud** (`bitbucket.org`) | Repository Token `<token>`, or `username:app_password` | `x-token-auth:<token>` (or `username:app_password`) |
| **Any HTTPS Git Server** | `username:password` (explicit colon) | `username:password` |

### Using SSH Keys Instead of `LOBE_GIT_TOKEN`

If you use SSH remote URLs (for example, `git@github.com:user/lobe.git` or `ssh://git@codeberg.org/user/lobe.git`):
1. Leave `LOBE_GIT_TOKEN` unset.
2. Mount your SSH key and `known_hosts` file into `/root/.ssh` (read-only) inside the container.

### Per-Lobe Git Settings

Each lobe stores its remote settings in its own `.git/config`:
- `remote.origin.url`: Remote Git repository URL.
- `lobe.autopush`: `true` or `false`. When `true`, every write operation (`add_raw`, `write_page`) pushes the commit to `origin` (`HEAD`).

When you configure a remote (`create_lobe` with `remote_url` or `configure_remote`), `lobe-server` runs `git ls-remote` first to verify that the repository exists and that your credentials are valid.

---

## HTTP REST API Reference

 The complete OpenAPI 3.1 specification is in [`openapi.yaml`](../openapi.yaml). When `LOBE_API_TOKEN` is set, pass `Authorization: Bearer <LOBE_API_TOKEN>` in every request to `/api/*` and `/mcp`.

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Public health check. Returns `{"status": "ok"}`. |
| `GET` | `/api/lobes` | List all lobes in `LOBES_DIR` with remote URL and `autopush` status. |
| `POST` | `/api/lobes` | Create a new lobe or clone an existing non-empty `remote_url`. Body: `{"name", "purpose", "remote_url?", "autopush?"}`. |
| `PUT` | `/api/lobes/{lobe}/remote` | Set or update `origin` remote URL and `autopush` flag. Body: `{"remote_url?", "autopush?"}`. |
| `POST` | `/api/lobes/{lobe}/sync` | Fetch and rebase (`git pull --rebase`) from `origin`, and optionally push. Body: `{"push?"}`. Returns `409 Conflict` and aborts cleanly if a rebase conflict occurs. |
| `GET` | `/api/lobes/{lobe}/search?q=a|b&limit=5` | Search wiki pages (`q` accepts pipe-separated terms; repeat `q` for batch search). |
| `GET` | `/api/lobes/{lobe}/links/{page}` | List incoming and outgoing `[[wikilinks]]` for `{page}`. |
| `GET` | `/api/lobes/{lobe}/sections` | List `## Section` headings in `wiki/index.md`. |
| `GET` | `/api/lobes/{lobe}/pending` | List files in `raw/` that no wiki page cites in `sources:`. |
| `GET` | `/api/lobes/{lobe}/lint?strict=true` | Run deterministic wiki health checks (`returncode: 0` when clean). |
| `POST` | `/api/lobes/{lobe}/raw` | Write a raw source file to `raw/` and commit. Body: `{"filename", "content", "message?"}`. |
| `GET` | `/api/lobes/{lobe}/pages/{slug}` | Read `wiki/{slug}.md` and return its content. |
| `PUT` | `/api/lobes/{lobe}/pages/{slug}` | Create or update `wiki/{slug}.md`, optionally register it in `wiki/index.md` (`section` + `description`), and commit. Body: `{"content", "section?", "description?", "message?"}`. |
| `POST` | `/mcp` | Streamable HTTP Model Context Protocol (MCP) JSON-RPC 2.0 endpoint. |

### REST API Examples

```bash
# 1. Create or clone a lobe with auto-push enabled
curl -s -X POST https://lobes.example.com/api/lobes \
  -H "Authorization: Bearer $LOBE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "hack",
    "purpose": "Device and home-network tinkering",
    "remote_url": "https://github.com/example/hack.git",
    "autopush": true
  }'

# 2. Search a lobe
curl -s "https://lobes.example.com/api/lobes/hack/search?q=pi5|nvme&limit=5" \
  -H "Authorization: Bearer $LOBE_API_TOKEN"

# 3. Synchronize (pull --rebase + push) with origin
curl -s -X POST https://lobes.example.com/api/lobes/hack/sync \
  -H "Authorization: Bearer $LOBE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"push": true}'
```

---

## Model Context Protocol (MCP) Reference

`lobe-server` implements MCP protocol version `2025-06-18` over Streamable HTTP (`POST /mcp`) and standard input/output (`--stdio`).

### Available MCP Tools

| Tool Name | Required Arguments | Optional Arguments | Description |
| :--- | :--- | :--- | :--- |
| `lobe_list` | *(none)* | *(none)* | List all lobes on the server. |
| `lobe_create` | `name`, `purpose` | `remote_url`, `autopush` | Create a new lobe or clone an existing remote repository. |
| `lobe_configure_remote` | `lobe` | `remote_url`, `autopush` | Configure the Git `origin` URL and `lobe.autopush` setting. |
| `lobe_sync` | `lobe` | `push` | Run `git fetch` and `git pull --rebase` from `origin`, and push if enabled. |
| `lobe_search` | `lobe`, `terms` | `limit` | Search wiki pages (`terms` is an array of strings or `a|b` alternations). |
| `lobe_links` | `lobe`, `page` | *(none)* | Show incoming and outgoing `[[wikilinks]]` for a page. |
| `lobe_sections` | `lobe` | *(none)* | List `## Section` headings in `wiki/index.md`. |
| `lobe_pending` | `lobe` | *(none)* | List raw source files in `raw/` that no wiki page cites yet. |
| `lobe_lint` | `lobe` | `strict` | Run deterministic `wiki_tool.py lint` checks. |
| `lobe_add_raw` | `lobe`, `filename`, `content` | `message` | Save a source file in `raw/`, commit to Git, and push if enabled. |
| `lobe_read_page` | `lobe`, `slug` | *(none)* | Read the markdown content of `wiki/<slug>.md`. |
| `lobe_write_page` | `lobe`, `slug`, `content` | `section`, `description`, `message` | Write `wiki/<slug>.md`, optionally add to `wiki/index.md`, commit, and push if enabled. |

---

## Connecting AI Clients to `lobe-server`

### 1. Claude Code (`claude` / `claudeme`)

Claude Code supports native HTTP MCP servers. Run the following command for each Claude Code profile:

```bash
# Default Claude Code installation (~/.claude.json)
claude mcp add --transport http --scope user lobe-server https://lobes.example.com/mcp \
  --header "Authorization: Bearer <LOBE_API_TOKEN>"

# Custom profile (for example, ~/.claude-personal)
CLAUDE_CONFIG_DIR="$HOME/.claude-personal" claude mcp add --transport http --scope user \
  lobe-server https://lobes.example.com/mcp \
  --header "Authorization: Bearer <LOBE_API_TOKEN>"
```

This adds the following entry to `~/.claude.json`:

```json
{
  "mcpServers": {
    "lobe-server": {
      "type": "http",
      "url": "https://lobes.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <LOBE_API_TOKEN>"
      }
    }
  }
}
```

### 2. Antigravity (`agy` / `agyme` / `agydh`) and Claude Desktop

For clients that launch MCP servers as local `stdio` commands (or to avoid self-signed TLS certificate issues across runtimes), create a small stdio-to-HTTPS bridge script at `~/.local/bin/lobe-mcp-https`:

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

Add `lobe-server` to the MCP configuration files:
- **Antigravity (`agy` / `agydh`)**: `~/.gemini/config/mcp_config.json`
- **Antigravity Personal (`agyme`)**: `~/.gemini-personal/.gemini/config/mcp_config.json`
- **Claude Desktop (macOS)**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "lobe-server": {
      "command": "/Users/<you>/.local/bin/lobe-mcp-https",
      "args": [],
      "env": {
        "LOBE_MCP_URL": "https://lobes.example.com/mcp",
        "LOBE_API_TOKEN": "<LOBE_API_TOKEN>"
      }
    }
  }
}
```

### 3. Local Stdio Mode (No HTTP Server Needed)

If your lobes are stored on the same machine as your AI client, you can run `server/app.py --stdio` directly without Docker or HTTP:

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
