# lobe-forge

[![Release](https://img.shields.io/github/v/release/MinimalOne/lobe-forge)](https://github.com/MinimalOne/lobe-forge/releases)
[![GHCR workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://docs.claude.com/en/docs/claude-code/plugins)

**A shared memory for your AI tools: wikis that your AI writes, you own, and every AI tool you use can read.**

You collect things worth keeping: a web page, a PDF, meeting notes, a chat. Your AI assistant reads them and writes tidy, linked wiki pages that say where each fact came from. Later you ask a question, and the assistant answers from those pages and cites them.

Each topic gets its own wiki, called a **lobe**: `homelab`, `work`, `family`, `german`. A lobe is an ordinary folder of text files with full version history (Git), so nothing is locked inside an app and every change can be undone.

- **Works with the AI you already use**, through the Model Context Protocol (MCP):
  - AI coding tools: Claude Code, Codex CLI, Gemini CLI, Antigravity, Cursor, VS Code
  - AI chat apps: Claude.ai, Claude Desktop, Claude mobile
  - Personal AI agents: OpenClaw, Hermes Agent
- **One memory, many tools.** A fact one assistant saves today, another can answer from tomorrow.
- **On your computer or your own server.** Keep lobes on your computer, or run `lobe-server` on a home server and reach them from every device, including your phone.
- **Yours.** Plain Markdown files in Git. Push them to any Git host, or keep them entirely offline.

![Demo: create a lobe, add Karpathy's LLM wiki gist, ingest it, ask a question](docs/demo.gif)

## Words you'll see

| Word | What it means |
| :--- | :--- |
| **Lobe** | One wiki about one topic. It is a folder on disk that is also a Git repository. |
| **Source** | Something you add, such as a web page, PDF or note. It is kept unchanged in the lobe's `raw/` folder. |
| **Page** | A wiki page the AI writes from your sources, in Markdown (plain text with simple formatting). |
| **Ingest** | Asking the AI to read new sources and turn them into pages. |
| **Lint** | A health check of the wiki: broken links, outdated pages, pages no person has reviewed. |
| **Verify** | A person confirming that a page is correct. Only a person can do this, never the AI. |
| **MCP** | Model Context Protocol, the standard way AI tools connect to outside data. |
| **Git** | Version history for files. Every change is saved, can be undone, and can be copied to GitHub or another host. |
| **lobe-server** | An optional program that hosts your lobes on a server, so all your devices and AI tools reach the same lobes. |
| **Token** | A long secret password that an AI tool sends to lobe-server to prove it may connect. |

## Why it matters

Letting an AI keep your notes is easy. Trusting those notes a year later is not. An AI-written wiki goes wrong in quiet ways, and lobe-forge is built around the three most common:

- **You can't tell which pages a person checked.** lobe-forge records on every page who last wrote it and whether a person reviewed it. An AI cannot mark its own work as reviewed, and any later change shows the page as "changed since review".
- **A page that was right in spring is wrong by autumn.** Each page has a type with its own shelf life (a how-to goes stale sooner than a decision record), and lint lists what is due for a check.
- **The knowledge ends up locked in one app.** Pages are plain Markdown in Git, and a whole lobe exports to the open [Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format).

## What you get

- **Answers with sources.** Every claim on a page cites the source it came from, and answers cite the pages they used.
- **Search that finds the right page.** Matches in a page's title, aliases and one-line facts ("atoms": commands, fixes, decisions) rank above matches in the body. Assistants read only the section they need and edit pages in place instead of rewriting them.
- **Provenance and human review.** `generated` and `verified` stamps that only the server writes, and a review that only a person can give. [How trust works](docs/how-it-works.md#trust-who-wrote-a-page-and-did-a-person-check-it)
- **Shelf life by page type.** Note, Runbook, Reference, Decision, Session and Todo, each with a default staleness period you can override per page. [Page types](docs/how-it-works.md#page-types-and-staleness)
- **A health check.** Lint finds broken links, orphan pages, index drift, likely duplicates, stale pages and pages changed since review, and ignores anything inside code blocks. [Lint](docs/how-it-works.md#lint-keeping-the-wiki-healthy)
- **Safe when several tools write at once.** Every change is a Git commit. Writes take turns, and a write based on an outdated copy of a page is refused instead of overwriting newer work.
- **Open export.** `export-okf` turns a lobe into an OKF v0.2 bundle that any tool can read. [Export](docs/how-it-works.md#export-open-knowledge-format)
- **Nothing extra to install.** The wiki tools and lobe-server use only the Python standard library, so they run anywhere Python 3.10 and Git do, including a Raspberry Pi. The optional `lobectl` command-line tool is a single Go binary.

## Choose how to run it

| | On your computer (local lobes) | On your own server (remote lobes) |
| :--- | :--- | :--- |
| Where lobes live | `~/lobes/<name>` on your computer | on your server, with `lobe-server` in Docker |
| How AI tools connect | the Claude Code plugin, or any MCP tool on the same computer | any MCP tool, over the internet (HTTPS) |
| Best for | one computer, everything offline | several devices, phone access, many AI tools |
| Guide | [Local lobes](docs/local.md) | [Self-hosted lobe-server](docs/server.md) |

Not sure? Start on your computer. Both use the same lobe format, so a lobe can move to a server later.

## Quick start

### On your computer, with Claude Code

You need Claude Code, Git, and Python 3.10 or newer.

1. Install the plugin:

   ```bash
   claude plugin marketplace add MinimalOne/lobe-forge
   claude plugin install lobe-forge@lobe-forge
   ```

2. In Claude Code, run `/lobe-forge`. Give the lobe a name (for example `homelab`) and say what it is for.
3. Restart Claude Code.

Now talk to it in plain words: *"save this to homelab"*, *"ask homelab how the reverse proxy is set up"*. Each lobe also gets four slash commands, for example `/homelab-add-raw`, `/homelab-ingest`, `/homelab-ask` and `/homelab-lint`.

To use the same lobes from Codex, Gemini CLI, Antigravity or another MCP tool, see [Local lobes without a server](docs/clients.md#local-lobes-without-a-server).

### On your own server, with `lobe-server`

You need Docker. Replace `change-me` with a long random secret (for example from `openssl rand -hex 32`): it is the token your AI tools use to connect.

```bash
docker run -d --name lobe-server -p 127.0.0.1:8080:8080 \
  -e LOBE_API_TOKEN=change-me -v ~/lobes:/data/lobes \
  ghcr.io/minimalone/lobe-forge:latest

claude mcp add --transport http --scope user lobe-server http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer change-me"
```

Then ask your assistant: *"Create a `homelab` lobe on lobe-server for my server notes."*

This only listens on the same machine. To reach it from other devices, with HTTPS, Git backup and sign-in for Claude.ai and mobile, follow [docs/server.md](docs/server.md). To connect other AI tools, see [docs/clients.md](docs/clients.md).

## Documentation

| Guide | Read it when you want to |
| :--- | :--- |
| [Connect AI tools](docs/clients.md) | Add your lobes to Claude, Codex, Gemini CLI, Antigravity, Cursor, VS Code, OpenClaw or Hermes Agent |
| [Local lobes](docs/local.md) | Create, adopt, move, upgrade or remove lobes on your computer |
| [Self-hosted lobe-server](docs/server.md) | Host lobes on a server with Docker, Git push, sign-in (OAuth) and human review |
| [How it works](docs/how-it-works.md) | Understand what is inside a lobe, local versus server, trust stamps, page types, lint, export and Git sync |
| [Reference](docs/reference.md) | Look up settings, MCP tools, REST endpoints and `lobectl` commands |
| [OpenAPI spec](openapi.yaml) | Generate a REST client |
| [Contributing](docs/CONTRIBUTING.md) | Run the tests and understand releases |

## License

[MIT](LICENSE)
