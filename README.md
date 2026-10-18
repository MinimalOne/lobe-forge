# lobe-forge

[![Release](https://img.shields.io/github/v/release/MinimalOne/lobe-forge)](https://github.com/MinimalOne/lobe-forge/releases)
[![Release workflow](https://github.com/MinimalOne/lobe-forge/actions/workflows/release.yml/badge.svg)](https://github.com/MinimalOne/lobe-forge/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](https://docs.claude.com/en/docs/claude-code/plugins)

A Claude Code plugin that creates **lobes**: git-backed LLM wikis, one per purpose (personal,
family, career, an idea), each with its own skills named after it.

```
/lobe-forge  →  name: personal
             →  /personal-add-raw   save a URL, file (PDF, image, doc) or text into raw/
                /personal-ingest    turn a source into interlinked wiki pages
                /personal-ask       answer from the wiki, with citations
                /personal-lint      health check: format, links, index, stale, contradictions
```

## Why

Based on Karpathy's [LLM wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
pattern: instead of re-reading raw documents on every question, Claude compiles them once into
interlinked wiki pages and answers from those. lobe-forge adds:

- **One wiki per purpose**, each with its own named skills, so `/family-ask` never answers from
  your work notes.
- **Token-lean retrieval**: a bundled `wiki_tool.py` ranks pages by title, alias, summary and
  body, so skills read search hits instead of the whole index.
- **Full history**: every ingest is a git commit.
- **Adoption** of wikis you already have, without rewriting a page.

## Quick start

Requires [Claude Code](https://docs.claude.com/en/docs/claude-code), git and Python 3.10+
(standard library only). The [GitHub CLI](https://cli.github.com) is optional, for publishing
a lobe.

```bash
claude plugin marketplace add MinimalOne/lobe-forge     # or a local clone path
claude plugin install lobe-forge@lobe-forge
```

Then run `/lobe-forge`. It asks for:

1. **Name**: prefixes every skill (`personal` → `personal-ask` …)
2. **Purpose**: one line, written into each skill's description
3. **Repo**: create a new one, or use an existing local clone / GitHub URL
4. **Claude installations**: e.g. `~/.claude` and `~/.claude-personal`

Restart Claude Code, then feed it a source: `/personal-add-raw <url or file>`.

## How it works

A lobe repo:

```
raw/            sources, append-only
wiki/           pages + index.md + log.md
wiki_tool.py    search, links, sections, index-add, lint, build, convert-legacy (stdlib Python)
wikictl         wrapper that points wiki_tool.py at this lobe's folders
LOBE.md         page format and rules
skills/         <name>-add-raw, -ingest, -ask, -lint
```

The skills are **symlinked** into each Claude installation and find their repo by resolving
that link, so a lobe repo works wherever it is cloned.

## Usage

### Adopt existing wikis

Give `/lobe-forge` an existing repo, or a folder of repos, and it scans for `llmwiki/` or
`wiki/` folders with an `index.md`, then offers to adopt each one as a named lobe. The wiki's
layout stays as it is (e.g. `llmwiki/` + `docs/`), and no page is rewritten.

- **In place** (your own repos): lobe files are added to the repo and committed with it.
- **External** (repos owned by someone else, e.g. an employer org): lobe files go to
  `~/lobes/_adopted/<name>/` with a `repo` link back. Nothing is written into the repo, and the
  generated skills ask before every commit and never push.

```bash
python3 scripts/forge.py scan ~/code      # read-only report
```

For each wiki the scan reports whether git tracks it (an ignored wiki has no backup), how many
people commit to its raw folder (more than one means a shared docs folder), and any installed
skill or plugin that already serves it.

### Install on another machine

Clone the lobe repo, then:

```bash
python3 scripts/forge.py install --repo <lobe-repo> --target ~/.claude
```

### Upgrade a lobe

`create` never overwrites, so existing lobes keep the tooling they were made with. After
updating lobe-forge, re-render a lobe's generated files (`wiki_tool.py`, `wikictl`, `LOBE.md`,
the four `skills/<name>-{add-raw,ingest,ask,lint}/SKILL.md`) from the current templates:

```bash
python3 scripts/forge.py upgrade --repo <lobe-repo-or-home>           # dry run: what would change
python3 scripts/forge.py upgrade --repo <lobe-repo-or-home> --apply
python3 scripts/forge.py upgrade --all [--apply]                       # every installed lobe
```

`--all` finds lobes from the skill links in your Claude config dirs. Other skills in a lobe's
`skills/` (your own, not forge's) are never touched, nor is a forge skill you rewrote by
hand (one that no longer runs `wikictl`). If all four are rewritten, the lobe runs its own
tooling and upgrade leaves every file alone. A forge skill the lobe lacks in a Claude dir
that already has the lobe installed (a new skill kind) is linked there too.

Name, purpose, folders and origin are read back from the lobe itself. Wiki pages and sources are
never touched. In place, the files must be committed first so git holds the old version; an
external home gets `*.bak` copies instead.

### Uninstall or move a lobe

```bash
python3 scripts/forge.py uninstall --repo <lobe>              # remove its skill links everywhere
python3 scripts/forge.py uninstall --repo <home> --purge      # external: also delete the home
```

Only links that point into that lobe are removed. `--purge` is for external homes only and
deletes nothing if the home holds files forge did not generate.

## Safety

- `create` only adds what is missing. Only `upgrade --apply` overwrites, and `uninstall`
  deletes, and only what forge itself made — never wiki pages or sources.
- `LOBE.md` records the lobe's **Origin**: `new`, the adopted repo's remote, or `--origin` (on
  `create` or `upgrade`). `/lobe-forge` offers to publish a lobe to your GitHub only when its
  content is yours, so a wiki adopted from an employer repo is never pushed to a personal
  account. Published lobes are always private unless you ask otherwise.

## Contributing

Issues and pull requests are welcome. Run the tests before sending a change:

```bash
python3 test_forge.py
```

Use [conventional commits](https://www.conventionalcommits.org): they drive releases.

### Releasing

Each push to `main` runs `.github/workflows/release.yml`. If `.claude-plugin/plugin.json` was
bumped by hand past the last `v*` tag, that version is released. Otherwise conventional
commits since the last tag that touch `skills/`, `scripts/` or `templates/` pick the bump:
`feat:` minor, `fix:`/`perf:`/`refactor:`/`docs:` patch, `type!:` or `BREAKING CHANGE` major;
other types release nothing. The workflow writes the version into `plugin.json` and
`skills/lobe-forge/SKILL.md`, commits, tags and publishes a GitHub release.

## License

[MIT](LICENSE)
