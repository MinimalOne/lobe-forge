# lobe-forge

[![Release](https://img.shields.io/github/v/release/MinimalOne/lobe-forge)](https://github.com/MinimalOne/lobe-forge/releases)
[![GHCR workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://docs.claude.com/en/docs/claude-code/plugins)

**Give your AI assistant a memory it keeps for you: one Git-backed wiki per topic.**

A **lobe** is a small wiki that your AI assistant writes and maintains, following Karpathy's "LLM wiki" pattern. You drop in sources (web pages, PDFs, notes). The assistant turns them into linked Markdown pages and answers questions from them with citations. Each lobe is its own Git repository, so everything is versioned, private, and readable without any tool.

Every lobe gets four commands named after it. For a lobe called `personal`:

| Command | What it does |
| :--- | :--- |
| `/personal-add-raw <url-or-file>` | Save a source into `raw/` |
| `/personal-ingest` | Turn a saved source into linked wiki pages |
| `/personal-ask <question>` | Answer from the wiki, with citations |
| `/personal-lint` | Check for broken links, orphan pages, and stale index entries |

![Demo: create a lobe, add Karpathy's LLM wiki gist, ingest it, ask a question](docs/demo.gif)

## Quick start

Pick where your lobes live. You can switch later: both ways use the same Git repositories.

### On your computer (Claude Code plugin)

Needs Claude Code, Git, and Python 3.10 or newer.

```bash
claude plugin marketplace add MinimalOne/lobe-forge
claude plugin install lobe-forge@lobe-forge
```

Run `/lobe-forge` in Claude Code, give the lobe a name and a purpose, and restart Claude Code. The four `/<name>-*` commands are now available.

More: [docs/local.md](docs/local.md)

### On a server (`lobe-server`, for any MCP client)

Run `lobe-server` on a home server, Raspberry Pi, or VPS, and connect any assistant that supports MCP. No wiki files are kept on your laptop.

```bash
docker run -d --name lobe-server -p 127.0.0.1:8080:8080 \
  -e LOBE_API_TOKEN=change-me -v ~/lobes:/data/lobes \
  ghcr.io/minimalone/lobe-forge:latest

claude mcp add --transport http --scope user lobe-server http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer change-me"
```

Then ask your assistant: *"Create a `personal` lobe on lobe-server for my notes and install its skills."*

More: [docs/server.md](docs/server.md) · Connect Claude.ai, Claude mobile, Cursor, VS Code, Codex, or Gemini CLI: [docs/clients.md](docs/clients.md)

## Documentation

| Guide | Read it when you want to |
| :--- | :--- |
| [Local lobes](docs/local.md) | Create, adopt, move, upgrade, or remove lobes on your computer |
| [Run lobe-server](docs/server.md) | Host lobes on a server with Docker, Git push, and OAuth |
| [Connect AI assistants](docs/clients.md) | Add lobe-server to Claude Code, Claude.ai and mobile, Cursor, VS Code, Codex, or Gemini CLI |
| [How it works](docs/how-it-works.md) | Understand the lobe layout, the two ways to run, and Git sync |
| [Reference](docs/reference.md) | Look up environment variables, MCP tools, and REST endpoints |
| [OpenAPI spec](openapi.yaml) | Generate a REST client |
| [Contributing](docs/CONTRIBUTING.md) | Run tests and release |

## License

[MIT](LICENSE)
