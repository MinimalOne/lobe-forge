# lobe-forge

[![Release](https://img.shields.io/github/v/release/MinimalOne/lobe-forge)](https://github.com/MinimalOne/lobe-forge/releases)
[![Release workflow](https://img.shields.io/github/v/release/MinimalOne/lobe-forge?label=release)](https://github.com/MinimalOne/lobe-forge/actions/workflows/release.yml)
[![GHCR workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/ghcr.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://docs.claude.com/en/docs/claude-code/plugins)

`lobe-forge` creates and manages **lobes** — Git-backed LLM wikis where each topic (for example: `personal`, `family`, `career`, or `hack`) gets its own isolated repository and its own four named skills:
- `/<name>-add-raw` — Save a URL, file (PDF, image, doc), or text note into `raw/`
- `/<name>-ingest` — Compile a raw source into linked wiki pages, `index.md`, and `log.md`
- `/<name>-ask` — Search and answer questions from the wiki with citations
- `/<name>-lint` — Audit frontmatter, broken links, orphans, index drift, and contradictions

![Demo: create a lobe, add Karpathy's LLM wiki gist, ingest it, ask a question](docs/demo.gif)

---

## Architecture: Two Ways to Run `lobe-forge`

`lobe-forge` supports two operating modes. The generated `<name>-*` skills automatically use the backend that created them:

| Mode | Managed By | Where Lobe Files Live | How `<name>-*` Skills Run |
| :--- | :--- | :--- | :--- |
| **1. Local Lobes** | `/lobe-forge` plugin (`scripts/forge.py`) | Local disk (`~/lobes/<name>`) | Skills (`templates/skills/`) run local `wikictl` and local `git` commands. |
| **2. Remote Lobes** | `lobe-server` MCP server (`server/app.py`) | Remote server (`/data/lobes/<name>`) | Skills (`templates/mcp-skills/`) call `lobe-server` MCP tools over HTTPS or stdio. No local lobe clone is needed. |

```text
MODE 1: LOCAL LOBES (/lobe-forge plugin)
────────────────────────────────────────
  /lobe-forge  ──►  Creates ~/lobes/<name> + symlinks local skills (~/.claude/skills/<name>-*)
  /<name>-*    ──►  Runs local wikictl & git directly on ~/lobes/<name>


MODE 2: REMOTE LOBES (lobe-server MCP)
──────────────────────────────────────
  lobe_create / lobe_generate_skills (MCP)
       ├──►  1. Creates or clones /data/lobes/<name> on lobe-server (auto-commits & optional git push)
       └──►  2. Generates remote-MCP skills (<name>-add-raw, -ingest, -ask, -lint) on your client machine
  /<name>-*  ──►  Calls lobe-server MCP tools (lobe_search, lobe_read_page, lobe_write_page, lobe_lint)
```

---

## Mode 1: Local Lobes (`/lobe-forge` Plugin)

Use Mode 1 when you want your wiki repositories stored on your local computer.

### Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code)
- Git
- Python 3.10 or newer

### Quick Start (Local)

1. Install the plugin in Claude Code:

   ```bash
   claude plugin marketplace add MinimalOne/lobe-forge
   claude plugin install lobe-forge@lobe-forge
   ```

2. Run `/lobe-forge` in Claude Code.
3. Enter the lobe **name**, **purpose**, **local repository path** (`~/lobes/<name>`), and target **Claude installations**.
4. Restart Claude Code to load the new skills, then use them:

   ```text
   /personal-add-raw https://example.com/article
   /personal-ingest
   /personal-ask what did that article say about X?
   /personal-lint
   ```

---

## Mode 2: Remote Lobes (`lobe-server` MCP Server)

Use Mode 2 when you host your lobes on a remote machine (for example, a Raspberry Pi or home server) and want to access them from Claude Code, Antigravity, or Claude Desktop without keeping local wiki clones on your client devices.

### 1. Deploy `lobe-server`

GitHub Actions publishes multi-architecture (`linux/amd64`, `linux/arm64`) Docker images to `ghcr.io/minimalone/lobe-forge` on every semantic release (`<X.Y.Z>`, `v<X.Y.Z>`, `<X.Y>`, `latest`):

```bash
# Pull the latest release or a specific semantic version (for example: 1.9.0)
docker pull ghcr.io/minimalone/lobe-forge:latest

# Start lobe-server with Docker Compose
docker compose -f server/docker-compose.yml up -d
```

#### Remote Git Token (`LOBE_GIT_TOKEN`) Permissions

If you configure `LOBE_GIT_TOKEN` so `lobe-server` can clone, pull, and push lobe repositories over HTTPS, grant only the minimum repository permissions on the token form. For example, for a **GitHub Fine-Grained Personal Access Token**:
- **Contents**: `Read and write`
- **Metadata**: `Read-only` (required, added automatically)
- **Everything else**: `No access`

See [Git Configuration and Authentication in `docs/usage.md`](docs/usage.md#git-configuration-and-authentication) for Codeberg/Forgejo, GitLab, Bitbucket, and SSH key setups.

### 2. Connect Your AI Clients to `lobe-server`

```bash
# Claude Code (native HTTP MCP transport)
claude mcp add --transport http --scope user lobe-server https://lobes.example.com/mcp \
  --header "Authorization: Bearer <LOBE_API_TOKEN>"
```

For Antigravity (`agy` / `agyme` / `agydh`) and Claude Desktop, see [Connecting AI Clients in `docs/usage.md`](docs/usage.md#connecting-ai-clients-to-lobe-server).

### 3. Create Remote Lobes and Generate Client Skills via MCP

With `lobe-server` connected as an MCP server, you can create remote lobes and install their `<name>-*` skills on your client machine in two ways:

1. **While creating a lobe (`lobe_create`)**:
   - Ask your AI assistant: *"Create a new `hack` lobe on lobe-server for homelab notes."*
   - The assistant asks whether you want to generate and install the four `<name>-*` skills (`hack-add-raw`, `hack-ingest`, `hack-ask`, `hack-lint`) locally.
   - When confirmed (`generate_skills: true`), `lobe_create` creates `/data/lobes/hack` on the server and returns the four rendered remote-MCP `SKILL.md` files, which the assistant writes into your local skill directories (`~/.claude/skills/`, `~/.gemini/config/skills/`, etc.).
2. **At any later time for an existing lobe (`lobe_generate_skills`)**:
   - Ask your AI assistant: *"Generate and install the skills for the `hack` lobe from lobe-server."*
   - The assistant calls `lobe_generate_skills(lobe="hack", mode="mcp")` and installs `hack-add-raw`, `hack-ingest`, `hack-ask`, and `hack-lint` into your local skill directories.
   - Those skills call `lobe-server` MCP tools (`lobe_add_raw`, `lobe_read_raw`, `lobe_search`, `lobe_read_page`, `lobe_write_page`, `lobe_lint`) directly.

---

## Common Commands Reference

### Manage Local Lobes with `/lobe-forge`

| Task | Command |
| :--- | :--- |
| Create a local lobe | `/lobe-forge create a family lobe for school, health, and holidays` |
| Scan a folder for `wiki/` or `llmwiki/` | `/lobe-forge scan ~/code` |
| Adopt existing wikis | `/lobe-forge adopt the wikis in ~/code` |
| Install a cloned lobe on a new machine | `/lobe-forge install the lobe I cloned to ~/lobes/personal` |
| Upgrade local lobe templates | `/lobe-forge upgrade the personal lobe` · `/lobe-forge upgrade my lobes` |
| Remove local lobe skill links | `/lobe-forge uninstall the personal lobe` |

### Manage Remote Lobes with `lobe-server` MCP

| Task | Prompt / MCP Tool |
| :--- | :--- |
| List all lobes on `lobe-server` | `lobe_list` |
| Create or clone a lobe on `lobe-server` (and optionally install skills) | `lobe_create(name, purpose, remote_url?, autopush?, generate_skills?)` |
| Generate `<name>-*` skills for an existing lobe on `lobe-server` | `lobe_generate_skills(lobe, mode="mcp" \| "local")` |
| Configure remote Git URL and auto-push | `lobe_configure_remote(lobe, remote_url, autopush)` |
| Pull and push changes with `origin` | `lobe_sync(lobe, push=true)` |

### Daily Lobe Skills (Identical Commands in Both Modes)

| Task | Command |
| :--- | :--- |
| Save a web page, local file, or note | `/<name>-add-raw https://example.com/article` |
| Compile a raw source into wiki pages | `/<name>-ingest raw/2026-10-30-notes.md` |
| Ask a question with citations | `/<name>-ask how is the reverse proxy configured?` |
| Audit the wiki | `/<name>-lint` |

---

## Documentation

- [Usage & Self-Hosted Server Reference (`docs/usage.md`)](docs/usage.md)
- [OpenAPI 3.1.0 Specification (`openapi.yaml`)](openapi.yaml)
- [Contributing Guide (`docs/CONTRIBUTING.md`)](docs/CONTRIBUTING.md)

## License

[MIT](LICENSE)
