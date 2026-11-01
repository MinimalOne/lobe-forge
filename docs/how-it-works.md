# How it works

lobe-forge gives your AI tools a shared, long-term memory: a second brain made of wikis that the AI writes and you own.

## A lobe is a Git repository

The idea comes from Karpathy's "LLM wiki" pattern. You add sources, and the assistant compiles them into a wiki that it keeps consistent: summaries, links between pages, an index, and a log. You read and steer it; the assistant does the writing and bookkeeping.

```text
raw/            Sources you added (append-only: never edited or deleted)
wiki/           Markdown pages, plus index.md (table of contents) and log.md (history; earlier months in wiki/log/)
wiki_tool.py    Search, links, sections, index, pending sources, lint
wikictl         Wrapper that runs wiki_tool.py on this lobe
LOBE.md         Page format and rules the assistant follows
skills/         <name>-add-raw, <name>-ingest, <name>-ask, <name>-lint
```

Pages are plain Markdown with YAML frontmatter (title, summary, aliases, atoms, sources, last updated) and `[[wiki-links]]`. Atoms are one-line facts, commands, fixes, and decisions, so search can answer from them directly. Search ranks a match in a page's name, title, aliases, atoms, or summary above a match in the body text, and slightly prefers recently updated pages. Every change is a Git commit, so you can review, revert, or push it like any other repository.

## Local and remote lobes

The lobe format is the same in both. What differs is where the files live and how AI tools reach them.

| | Local lobes | Remote lobes (self-hosted) |
| :--- | :--- | :--- |
| Lobe files | `~/lobes/<name>` on your computer | `/data/lobes/<name>` on your server |
| Created by | `/lobe-forge` Claude Code plugin (`scripts/forge.py`) | `lobe_create` on `lobe-server` (`server/app.py`) |
| Claude Code skills call | local `wikictl` and `git` (`templates/skills/`) | `lobe_*` MCP tools (`templates/mcp-skills/`) |
| Other AI tools connect | `server/app.py --stdio` on the same computer | `https://<server>/mcp` |

```text
LOCAL
  /lobe-forge          ──►  creates ~/lobes/<name>, links its skills into ~/.claude/skills/
  Claude Code skills   ──►  run wikictl and git on ~/lobes/<name>
  other MCP tools      ──►  start server/app.py --stdio with LOBES_DIR=~/lobes

REMOTE
  any MCP tool  ──►  https://<server>/mcp  ──►  lobe-server  ──►  /data/lobes/<name>
  lobe_create / lobe_generate_skills  ──►  can also return Claude Code skills that call lobe-server
```

Every way in reaches the same `lobe_*` operations (search, read, write, lint), so a lobe written by one tool can be read by any other.

## Git: commit always, push optionally

- **Local lobes**: every ingest creates a commit. Push when you want.
- **lobe-server**: every write (`lobe_add_raw`, `lobe_write_page`, `lobe_edit_page`) commits on the server. Writes run one at a time, so parallel clients never collide on Git. It pushes to `origin` only when the lobe has a remote and auto-push is on. `lobe_sync` fetches, rebases (`git pull --rebase`), and pushes. On a rebase conflict it aborts cleanly and returns `409 Conflict`.

Without a remote, everything stays on the machine that holds the lobe.

## Using local skills and lobe-server on the same lobe

Both operate on the same repository layout, so one lobe can be a local clone on your laptop and also live on lobe-server, with a shared Git remote between them:

1. Turn on auto-push on lobe-server, so every server-side change reaches `origin` right away.
2. Before you ingest locally, pull: `git -C ~/lobes/<name> pull --rebase`.
3. After you push local commits, ask the assistant to sync the lobe on lobe-server (`lobe_sync`).

| Operation | Local skill | lobe-server tools |
| :--- | :--- | :--- |
| Create or clone a lobe | `/lobe-forge` | `lobe_create` |
| Save a source | `/<name>-add-raw` | `lobe_add_raw` |
| Ingest a source | `/<name>-ingest` | `lobe_pending` → `lobe_search` → `lobe_read_page` → `lobe_sections` → `lobe_write_page` / `lobe_edit_page` → `lobe_lint` |
| Answer a question | `/<name>-ask` | `lobe_search` → `lobe_links` → `lobe_read_page` |
| Check wiki health | `/<name>-lint` | `lobe_lint` |
| Sync with the remote | `git pull --rebase && git push` | `lobe_sync` |
