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
- **Token-lean retrieval**: a bundled search tool ranks pages by title, alias, summary and body,
  so skills read search hits instead of the whole index.
- **Full history**: every ingest is a git commit.
- **Adoption** of wikis you already have, without rewriting a page.

## Quick start

Requires [Claude Code](https://docs.claude.com/en/docs/claude-code), git and Python 3.10+.

```bash
claude plugin marketplace add MinimalOne/lobe-forge
claude plugin install lobe-forge@lobe-forge
```

Run `/lobe-forge`. It asks for a **name** (prefixes every skill), a one-line **purpose**, a
**repo** (new, or an existing clone or GitHub URL) and which **Claude installations** to
install into. Restart Claude Code, then feed it a source:

```
/personal-add-raw https://example.com/article
/personal-ingest
/personal-ask what did that article say about X?
```

## Everyday tasks

Ask `/lobe-forge`:

| You want to | Say |
|---|---|
| Turn existing `wiki/` or `llmwiki/` folders into lobes | `/lobe-forge adopt the wikis in ~/code` |
| Use a lobe on another machine | `/lobe-forge install my lobe from ~/lobes/personal` |
| Get new templates after updating the plugin | `/lobe-forge upgrade my lobes` |
| Remove a lobe's skills | `/lobe-forge uninstall the personal lobe` |

Wikis in repos you don't own (e.g. an employer org) are adopted **externally**: nothing is
written into the repo, and the skills never push. lobe-forge only overwrites or deletes files
it generated, never wiki pages or sources, and shows a dry run first.

See [docs/usage.md](docs/usage.md) for the lobe layout, what each task does, and the
underlying `forge.py` commands.

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## License

[MIT](LICENSE)
