# lobe-forge

Create **lobes**: git-backed LLM wikis, one per purpose (personal, family, career, an idea),
each with its own Claude Code skills named after it.

```
/lobe-forge  →  name: personal
             →  /personal-add-raw   save a URL, file (PDF, image, doc) or text into raw/
                /personal-ingest    turn a source into interlinked wiki pages
                /personal-ask       answer from the wiki, with citations
                /personal-lint      health check: format, links, index, stale, contradictions
```

Based on Karpathy's LLM-wiki pattern, with token-lean retrieval: a bundled `wiki_tool.py`
ranks pages by title, alias, summary and body, so skills read search hits instead of the
whole index.

## Install

```bash
claude plugin marketplace add MinimalOne/lobe-forge     # or a local clone path
claude plugin install lobe-forge@lobe-forge
```

Then run `/lobe-forge`. It asks for:

1. **Name** — prefixes every skill (`personal` → `personal-ask` …)
2. **Purpose** — one line, written into each skill's description
3. **Repo** — create a new one, or use an existing local clone / GitHub URL
4. **Claude installations** — e.g. `~/.claude` and `~/.claude-personal`

## Adopting existing wikis

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

## What a lobe repo looks like

```
raw/            sources, append-only
wiki/           pages + index.md + log.md
wiki_tool.py    search, links, sections, index-add, lint, build (stdlib Python)
wikictl         wrapper that points wiki_tool.py at this lobe's folders
LOBE.md         page format and rules
skills/         <name>-add-raw, -ingest, -ask, -lint
```

The skills are **symlinked** into each Claude installation and find their repo by resolving
that link, so a lobe repo works wherever it is cloned. Every ingest is a git commit, so the
wiki has full history.

On another machine: clone the lobe repo, then

```bash
python3 scripts/forge.py install --repo <lobe-repo> --target ~/.claude
```

## Development

```bash
python3 test_forge.py
```

`forge.py` never overwrites or deletes files; re-running `create` only adds what is missing.
