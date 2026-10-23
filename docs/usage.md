# Usage reference

Everything here is what `/lobe-forge` does for you. Run the commands yourself only for scripting
or troubleshooting. `forge.py` lives in `scripts/` of a clone of this repo, or of the installed
plugin (`~/.claude/plugins/cache/lobe-forge/lobe-forge/<version>/`).

## A lobe repo

```
raw/            sources, append-only
wiki/           pages + index.md + log.md
wiki_tool.py    search, links, sections, index-add, set-atoms, pending, lint, build, convert-legacy (stdlib Python)
wikictl         wrapper that points wiki_tool.py at this lobe's folders
LOBE.md         page format and rules
skills/         <name>-add-raw, -ingest, -ask, -lint
```

The skills are **symlinked** into each Claude installation and find their repo by resolving
that link, so a lobe repo works wherever it is cloned. Every ingest is a git commit.

## Adopting existing wikis

`/lobe-forge` scans a repo, or a folder of repos, for `llmwiki/` or `wiki/` folders with an
`index.md`, then offers to adopt each one as a named lobe. The wiki's layout stays as it is
(e.g. `llmwiki/` + `docs/`), and no page is rewritten.

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

## Installing on another machine

Clone the lobe repo, then:

```bash
python3 scripts/forge.py install --repo <lobe-repo> --target ~/.claude
```

An external lobe's home is not versioned: rerun `create` with the same name, `--repo`,
`--wiki-dir`, `--raw-dir` and `--home` to regenerate it.

## Upgrading

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

## Uninstalling or moving

```bash
python3 scripts/forge.py uninstall --repo <lobe>              # remove its skill links everywhere
python3 scripts/forge.py uninstall --repo <home> --purge      # external: also delete the home
```

Only links that point into that lobe are removed. `--purge` is for external homes only and
deletes nothing if the home holds files forge did not generate.

To move a lobe (e.g. external → its own repo): move or copy the wiki, `uninstall --purge` the
old home, then `create` in the new repo with `--origin "<where the content came from>"`.

## Origin and publishing

`LOBE.md` records the lobe's **Origin**: `new`, the adopted repo's remote, or `--origin` (on
`create` or `upgrade`). `/lobe-forge` offers to publish a lobe to your GitHub only when its
content is yours, so a wiki adopted from an employer repo is never pushed to a personal
account. Published lobes are private unless you ask otherwise.
