# lobe-forge

[![Release](https://img.shields.io/github/v/release/MinimalOne/lobe-forge)](https://github.com/MinimalOne/lobe-forge/releases)
[![Release workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/release.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/release.yml)
[![GHCR workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://docs.claude.com/en/docs/claude-code/plugins)

`lobe-forge` is a Claude Code plugin and self-hosted server that creates and manages **lobes**.
A lobe is a Git-backed LLM wiki for one topic (for example: personal, family, career, or homelab).
Each lobe has its own named skills (`<name>-add-raw`, `<name>-ingest`, `<name>-ask`, `<name>-lint`).

![Demo: create a lobe, add Karpathy's LLM wiki gist, ingest it, ask a question](docs/demo.gif)

```text
/lobe-forge  →  name: personal
             →  /personal-add-raw   Save a URL, file (PDF, image, doc), or text into raw/
                /personal-ingest    Compile a raw source into linked wiki pages
                /personal-ask       Answer questions from the wiki with citations
                /personal-lint      Check page format, links, index, and contradictions
```

## Key Features

This project uses the [LLM wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and adds these capabilities:

- **Topic Isolation**: Create one wiki per topic. `/family-ask` does not read your work notes.
- **Fast Search**: Rank pages with BM25F field weights (`title`, `aliases`, `atoms`, `summary`, `body`) and recency decay.
- **Git Version Control**: Record every raw source and wiki update as a Git commit.
- **Wiki Adoption**: Adopt existing `wiki/` or `llmwiki/` folders without page changes.
- **Self-Hosted Server (`lobe-server`)**: Host lobes on a remote machine over a REST API and Model Context Protocol (MCP) server.

## Quick Start (Local Plugin)

### Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code)
- Git
- Python 3.10 or newer

### Installation

1. Add the marketplace and install the plugin:

   ```bash
   claude plugin marketplace add MinimalOne/lobe-forge
   claude plugin install lobe-forge@lobe-forge
   ```

2. Run `/lobe-forge` in Claude Code.
3. Enter a lobe **name**, a one-line **purpose**, a **repository path**, and the target **Claude installations**.
4. Restart Claude Code to load the new skills.
5. Add and ingest your first source:

   ```text
   /personal-add-raw https://example.com/article
   /personal-ingest
   /personal-ask what did that article say about X?
   ```

## Quick Start (Self-Hosted Remote Server)

You can run `lobe-server` in Docker to manage lobes remotely through a REST API and an MCP server.
GitHub Actions builds multi-architecture (`linux/amd64`, `linux/arm64`) images and tags them with each semantic release version.

```bash
# Pull the latest release or a specific semantic version (for example: 1.9.0 or v1.9.0)
docker pull ghcr.io/minimalone/lobe-forge:latest

# Start lobe-server with Docker Compose
docker compose -f server/docker-compose.yml up -d
```

See [docs/usage.md](docs/usage.md) for all environment variables, Git token settings, REST API endpoints, and MCP client configurations.

## Using `/lobe-forge` Skills and the MCP Server Together

The `/lobe-forge` skills (`/<name>-add-raw`, `/<name>-ingest`, `/<name>-ask`, `/<name>-lint`) and the `lobe-server` MCP tools (`lobe_add_raw`, `lobe_write_page`, `lobe_search`, `lobe_read_page`, `lobe_lint`, `lobe_sync`) use the same `wiki_tool.py` engine and Git repository layout. You can combine them in three ways:

```text
  [Workstation: Claude Code / Antigravity]           [Other Devices: Claude Desktop / Mobile / IDEs]
      /<name>-add-raw, /<name>-ingest,                   MCP Tools: lobe_search, lobe_read_page,
      /<name>-ask, /<name>-lint                          lobe_add_raw, lobe_write_page, lobe_lint
                  │                                                          │
                  ▼ (git push / pull)                                        ▼ (HTTPS /mcp or stdio)
        [Git Remote (GitHub / Forgejo)] ◄────── lobe_sync / autopush ──────► [lobe-server (/data/lobes)]
```

### 1. Hybrid Setup (Local Skills + Remote MCP Server via Git Sync)

Use `/lobe-forge` skills on your main computer and `lobe-server` MCP tools across all your devices:

1. **Create and publish a lobe with `/lobe-forge`**:
   Run `/lobe-forge` in Claude Code to create `~/lobes/hack` and publish it to a remote Git repository (for example, `https://github.com/<you>/hack.git`).
2. **Clone the lobe onto `lobe-server`**:
   Call the `lobe_create` MCP tool (or `POST /api/lobes`) with `"name": "hack"`, `"remote_url": "https://github.com/<you>/hack.git"`, and `"autopush": true`. `lobe-server` clones the existing repository into `/data/lobes/hack`.
3. **Connect your AI tools to `lobe-server`**:
   ```bash
   claude mcp add --transport http --scope user lobe-server https://lobes.example.com/mcp \
     --header "Authorization: Bearer <LOBE_API_TOKEN>"
   ```
4. **Stay synchronized**:
   - When you write pages through MCP (`lobe_add_raw`, `lobe_write_page`), `lobe-server` commits locally and pushes to `origin` automatically.
   - When you run `/<name>-ingest` locally and push to `origin`, call the `lobe_sync` MCP tool (`POST /api/lobes/<name>/sync`) so `lobe-server` runs `git pull --rebase`.

### 2. Shared Local Directory (Skills + Stdio MCP on One Machine)

If you store your lobes in `~/lobes` on your computer and also use clients that do not load Claude Code skills (such as Claude Desktop), run `lobe-server --stdio` on the same `~/lobes` folder:

```bash
claude mcp add --transport stdio --scope user lobe-server \
  --env LOBES_DIR="$HOME/lobes" -- python3 /path/to/lobe-forge/server/app.py --stdio
```

Both `/<name>-*` skills and `lobe_*` MCP tools read and commit to `~/lobes/<name>` directly.

### 3. Remote-Only Setup (MCP Server Without Local Clones)

If you do not want local wiki files on a client device, install only the `lobe-server` MCP server. Use `lobe_create`, `lobe_add_raw`, `lobe_write_page`, `lobe_search`, `lobe_read_page`, and `lobe_lint` directly over HTTPS.

### Skill-to-MCP Tool Mapping

| `/lobe-forge` Skill | Equivalent `lobe-server` MCP Tools |
|---|---|
| `/lobe-forge` (create / adopt) | `lobe_create`, `lobe_list`, `lobe_configure_remote`, `lobe_sync` |
| `/<name>-add-raw` | `lobe_add_raw` |
| `/<name>-ingest` | `lobe_pending` → `lobe_search` → `lobe_read_page` → `lobe_sections` → `lobe_write_page` → `lobe_lint` |
| `/<name>-ask` | `lobe_search` → `lobe_links` → `lobe_read_page` |
| `/<name>-lint` | `lobe_lint` |

## Common Tasks

### Manage Lobes with `/lobe-forge`

| Task | Command |
|---|---|
| Create a lobe in a new repository | `/lobe-forge create a family lobe for school, health, and holidays` |
| Create a lobe in an existing repository | `/lobe-forge create a lobe in ~/code/my-project` |
| Scan a folder for `wiki/` or `llmwiki/` | `/lobe-forge scan ~/code` |
| Adopt existing wikis | `/lobe-forge adopt the wikis in ~/code` |
| Install a cloned lobe on a new machine | `/lobe-forge install the lobe I cloned to ~/lobes/personal` |
| Upgrade lobe templates | `/lobe-forge upgrade the personal lobe` · `/lobe-forge upgrade my lobes` |
| Move an adopted lobe to its own repository | `/lobe-forge move the team-notes lobe to ~/lobes/team-notes` |
| Remove lobe skills | `/lobe-forge uninstall the personal lobe` |

### Use a Lobe (Example: `personal`)

| Task | Command |
|---|---|
| Save a web page | `/personal-add-raw https://example.com/article` |
| Save a local file (PDF, image, doc) | `/personal-add-raw ~/Downloads/lease.pdf` |
| Save a text note | `/personal-add-raw dentist says next check-up in March` |
| Compile a raw source into wiki pages | `/personal-ingest raw/lease.pdf` |
| Ask a question with citations | `/personal-ask when does the lease end?` |
| Audit the wiki | `/personal-lint` |

## Documentation

- [Usage & Self-Hosted Server Reference](docs/usage.md)
- [OpenAPI 3.1.0 Specification](openapi.yaml)
- [Contributing Guide](docs/CONTRIBUTING.md)

## License

[MIT](LICENSE)
