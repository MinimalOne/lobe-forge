# How it works

lobe-forge gives your AI tools a shared, long-term memory: a second brain made of wikis that the AI writes and you own. New to the terms? See [Words you'll see](../README.md#words-youll-see).

## A lobe is a Git repository

The idea comes from Karpathy's "LLM wiki" pattern. You add sources, and the assistant compiles them into a wiki that it keeps consistent: summaries, links between pages, an index, and a log. You read and steer it; the assistant does the writing and bookkeeping.

A lobe is one folder:

```text
raw/            Sources you added (append-only: never edited or deleted)
wiki/           Markdown pages, plus index.md (table of contents) and log.md (history; earlier months in wiki/log/)
wiki_tool.py    Search, links, sections, index, pending sources, lint
wikictl         Wrapper that runs wiki_tool.py on this lobe
LOBE.md         Page format and rules the assistant follows
skills/         <name>-add-raw, <name>-ingest, <name>-ask, <name>-lint
```

Pages are plain Markdown. A small block of fields at the top of each page (YAML frontmatter) holds its title, type, summary, aliases, atoms, sources and last-updated date, and `[[wiki-links]]` link pages to each other. Atoms are one-line facts, commands, fixes, and decisions, so search can answer from them directly. Search ranks a match in a page's name, title, aliases, atoms, or summary above a match in the body text, and slightly prefers recently updated pages. Every change is a Git commit, so you can review, revert, or push it like any other repository.

## Trust: who wrote a page, and did a person check it

In short: the AI can write pages, but only a person can mark a page as checked, and that check lapses as soon as the page changes.

An AI wrote most of your wiki, so every page answers two questions in its frontmatter:

```yaml
generated: { by: "mcp-client", at: "2026-11-02T20:00:42Z" }                # the last write
verified:  { by: "human:alice", at: "2026-11-02T20:47:29Z", sha: "441f90d8d876da7c" }  # the last human review
```

- **lobe-server owns both fields.** It drops them from anything an agent writes, stamps `generated` itself on every change, and keeps `verified` from the page on disk. An agent cannot mark its own work as reviewed.
- **Only a person can verify.** `POST /api/lobes/<lobe>/pages/<slug>/verify` accepts only a human credential (`LOBE_HUMAN_TOKEN`, or an OAuth client or scope you mark as human); the agent token gets `403`, and there is no MCP tool for it. The request names the content hash you reviewed, so a page that changed in the meantime is refused with `409`. See [server.md](server.md#5-turn-on-human-page-verification-optional).
- **Reviews expire on change.** `verified.sha` is a hash of the page without the trust fields. Any later edit, by an agent, by you, or pulled in from Git, makes lint report the page as "changed since review", and the old review stays on the page as history.
- **Local lobes** stamp the same fields with `wikictl stamp` and `wikictl verify`. There, anyone with a shell can run them, so a local `verified` is an honest label, not a guarantee.

## Page types and staleness

Each page has a `type`, and the type decides how long the page stays fresh:

| Type | Goes stale after | For |
| :--- | :--- | :--- |
| `Note` | 90 days | Default; pages with no type age like notes |
| `Todo` | 90 days | Task lists |
| `Runbook` | 180 days | Step-by-step procedures |
| `Reference` | 365 days | Architecture, concepts, specs |
| `Decision` | never | Decisions and RFCs |
| `Session` | never | Dated records of one session or incident |

The clock runs from the latest of `last_updated`, the last edit, and the last human review, so touching or reviewing a page restarts it. `stale_after: YYYY-MM-DD` overrides the default, and `status: archived`, `deprecated` or `superseded` stops a page from ever going stale. `wikictl set-type --all` fills in types for existing pages.

## Lint: keeping the wiki healthy

Lint is a health check you can run at any time; the `<name>-lint` skill or a request like *"check my homelab lobe"* runs it for you. `wikictl lint` (or `lobe_lint`) checks every page. Broken `[[links]]`, frontmatter errors and missing source files are errors that block `--strict`; everything else is a warning:

- orphan pages, index drift, missing atoms, long pages, likely duplicates;
- missing or invalid page types (T), pages past their staleness date (S), and pages changed since their last human review (V).

Links and headings inside code blocks are ignored, so a `[[...]]` or `## comment` in a code sample is never mistaken for a real link or section.

## Export: Open Knowledge Format

`wikictl export-okf <dir>` (or `GET /api/lobes/<lobe>/export-okf`) writes the wiki as an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format) v0.2 bundle: `[[links]]` become ordinary Markdown links, `summary` becomes `description`, dates become full ISO 8601 timestamps, and the logs stay out. Other tools can read your knowledge without lobe-forge.

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

Every change is saved in the lobe's history. Copying that history to GitHub or another Git host (pushing) is your choice, and is what gives you an off-machine backup.

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
