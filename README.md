# lobe-forge

[![Release](https://img.shields.io/github/v/release/MinimalOne/lobe-forge)](https://github.com/MinimalOne/lobe-forge/releases)
[![GHCR workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://docs.claude.com/en/docs/claude-code/plugins)

**A second brain your AI tools share: Git-backed wikis that any AI agent can read, write, and search.**

You give it sources (web pages, PDFs, notes, chat logs). Your AI turns them into linked Markdown pages and answers from them with citations. Each topic is its own wiki, called a **lobe**: `homelab`, `work`, `family`, `german`. Every lobe is a plain Git repository, so your knowledge stays versioned, private, and readable without any tool.

- **Works with the AI you already use**, through the Model Context Protocol (MCP):
  - AI coding tools: Claude Code, Codex CLI, Gemini CLI, Antigravity, Cursor, VS Code
  - AI chat apps: Claude.ai, Claude Desktop, Claude mobile
  - Personal AI agents: OpenClaw, Hermes Agent
- **One memory, many tools.** A fact one assistant saves today, another can answer from tomorrow.
- **Local or self-hosted.** Keep lobes on your computer, or run `lobe-server` on a home server and reach them from every device, including your phone.
- **Yours.** Plain Markdown in Git. Push it to any Git host, or keep it entirely offline.

![Demo: create a lobe, add Karpathy's LLM wiki gist, ingest it, ask a question](docs/demo.gif)

## Two ways to run it

| | Local lobes | Remote lobes (self-hosted) |
| :--- | :--- | :--- |
| Lobes live | on your computer (`~/lobes/<name>`) | on your server (`lobe-server` in Docker) |
| Connect | Claude Code plugin, or any MCP client over stdio | any MCP client over HTTPS |
| Best for | one computer, everything offline | several devices, phone access, sharing between tools |
| Guide | [docs/local.md](docs/local.md) | [docs/server.md](docs/server.md) |

Both use the same lobe format. A lobe can start local and move to a server later.

## Quick start

### Local lobes with Claude Code

Needs Claude Code, Git, and Python 3.10 or newer.

```bash
claude plugin marketplace add MinimalOne/lobe-forge
claude plugin install lobe-forge@lobe-forge
```

Run `/lobe-forge`, give the lobe a name (for example `homelab`) and a purpose, and restart Claude Code. You can then say *"save this to homelab"* or *"ask homelab how the reverse proxy is set up"*. The lobe also gets slash commands: `/homelab-add-raw`, `/homelab-ingest`, `/homelab-ask`, `/homelab-lint`.

To use the same local lobes from Codex, Gemini CLI, Antigravity, or another MCP client, see [Local lobes from any MCP client](docs/clients.md#local-lobes-without-a-server).

### Remote lobes with `lobe-server`

```bash
docker run -d --name lobe-server -p 127.0.0.1:8080:8080 \
  -e LOBE_API_TOKEN=change-me -v ~/lobes:/data/lobes \
  ghcr.io/minimalone/lobe-forge:latest

claude mcp add --transport http --scope user lobe-server http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer change-me"
```

Then ask your assistant: *"Create a `homelab` lobe on lobe-server for my server notes."*

For a real deployment (TLS, Git push, OAuth for Claude.ai and mobile), see [docs/server.md](docs/server.md). To connect other tools, see [docs/clients.md](docs/clients.md).

## Documentation

| Guide | Read it when you want to |
| :--- | :--- |
| [Connect AI tools](docs/clients.md) | Add your lobes to Claude, Codex, Gemini CLI, Antigravity, Cursor, VS Code, OpenClaw, or Hermes Agent |
| [Local lobes](docs/local.md) | Create, adopt, move, upgrade, or remove lobes on your computer |
| [Self-hosted lobe-server](docs/server.md) | Host lobes on a server with Docker, Git push, and OAuth |
| [How it works](docs/how-it-works.md) | Understand the lobe format, local versus remote, and Git sync |
| [Reference](docs/reference.md) | Look up environment variables, MCP tools, and REST endpoints |
| [OpenAPI spec](openapi.yaml) | Generate a REST client |
| [Contributing](docs/CONTRIBUTING.md) | Run tests and release |

## License

[MIT](LICENSE)
