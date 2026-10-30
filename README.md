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
# Pull the latest release or a specific semantic version (for example: v1.10.0)
docker pull ghcr.io/minimalone/lobe-forge:latest

# Start lobe-server with Docker Compose
docker compose -f server/docker-compose.yml up -d
```

See [docs/usage.md](docs/usage.md) for all environment variables, Git token settings, REST API endpoints, and MCP client configurations.

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
