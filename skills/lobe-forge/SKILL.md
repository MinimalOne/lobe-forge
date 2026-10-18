---
name: lobe-forge
description: |
  Create a new lobe — a git-backed LLM wiki for one purpose (personal, family, career, an
  idea) — with its own named skills: <name>-add-raw, <name>-ingest, <name>-ask, <name>-lint.
  Asks for the lobe name, its purpose, whether to create a new repo or use an existing one, and
  which Claude installations to install into. Scans the given folder for existing llmwiki/ or
  wiki/ folders and offers to adopt them as named lobes. Also re-installs a lobe on a new machine.
  Trigger phrases: "/lobe-forge", "create a lobe", "new lobe", "forge a lobe",
  "new second brain wiki", "create wiki skills for", "install my lobe", "adopt my llmwiki".
version: 1.1.0
allowed-tools:
  - Bash
  - AskUserQuestion
---

# lobe-forge

Generate a lobe: a wiki repo plus four skills named after it. Example: name `personal` →
`/personal-add-raw`, `/personal-ingest`, `/personal-ask`, `/personal-lint`.

## Paths

`SKILL_BASE_DIR` is the path shown in the `Base directory for this skill:` header. Shell
variables do not persist between Bash calls — start every Bash command with:

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
```

All file work goes through `forge.py`; it never overwrites or deletes anything.

## Workflow

### Step 1 — Name and purpose

Ask (one `AskUserQuestion`, or inline if already given):

- **Name**: lowercase letters, digits, hyphens; starts with a letter; ≤30 chars (e.g.
  `personal`, `family`, `career`, `idea-x`). It prefixes every skill, so check it does not
  clash with an existing skill: `ls ~/.claude*/skills | grep -x "<name>-ask"`.
- **Purpose**: one line — what goes in this lobe. It is written into each skill's description,
  which is how Claude decides when to use the skill, so make it specific
  ("Family life: kids' school, health records, holidays, house admin").

### Step 2 — Repo, and existing wikis in it

Ask: **create a new repo** or **use an existing one** (local path, or a GitHub URL to clone with
`git clone <url> <path>`). For a new repo suggest `~/lobes/<name>` and skip to Step 3.

For an existing path, always scan it first — it may be a repo, or a folder of many repos:

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" scan "<path>"
```

Each hit prints `wiki_dir`, `raw_dir`, page count, how many pages are `legacy` (old body format,
no frontmatter), the git `remote`, and:

- `tracked` — `yes`, `untracked`, `ignored(<rule>)` or `no-git`. Anything but `yes` means the
  wiki is in no commit: it has **no backup**, and adopting it in place commits nothing of it.
  Say so before Step 3.
- `raw_authors` — people who committed to `raw_dir`. More than 1 means it is the repo's shared
  docs folder, not a private source folder: the lobe will read it, and `<name>-add-raw` would
  write into it. Say so, and suggest a separate raw folder for new sources if that matters.
- `managed_by` — an existing `*-ask` skill in the repo or an `adopted:<name>` home.
- `other_skills` — installed skills (`<config>:<skill>` or `<config>:<plugin>:<skill>`) that
  name this repo or its `<wiki_dir>/index.md`, e.g. a generic wiki plugin.

- **No hits** → a fresh lobe in that repo (Step 3, default `wiki/` + `raw/`).
- **Hits** → ask which to adopt (`AskUserQuestion`, multiSelect, one option per wiki: path,
  pages, managed_by), plus "none — create a fresh lobe". Recommend **not** adopting a wiki whose
  `managed_by` is not `-`: it already has skills, and a second set would compete with them.
  If `other_skills` is not `-`, adopting is fine, but those skills will compete with the new
  ones: offer to remove them afterwards (`claude plugin uninstall <plugin>@<marketplace>`, or
  the skill link), and do it only on a yes.
- For **each** wiki to adopt, ask its lobe name (suggest the repo name, e.g. `team-notes`) and
  purpose, and keep its layout: `--wiki-dir <wiki_dir> --raw-dir <raw_dir>`. If `raw_dir=?`, ask
  which folder holds its sources (often `docs`), or use `raw`.

**In place or external** — decide per repo, then confirm with the user:

```bash
gh api user -q .login          # your GitHub login
```

- Remote owner is your login, or there is no remote → **in place** (default): lobe files go
  into the repo and are committed with it.
- Remote owner is anything else (an employer org, someone else) → **external** (recommended):
  pass `--home ~/lobes/_adopted/<name>`. Lobe files go there, with a `repo` link back; the repo
  itself is not touched, and the generated skills ask before every commit and never push.

Adopting never rewrites existing pages. Legacy pages keep working; `<name>-lint` offers to
convert them with `wikictl convert-legacy` (dry run first, `--apply` on a yes).

### Step 3 — Where to install

List the Claude installations on this machine:

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" targets
```

Ask which to install into (`AskUserQuestion`, multiSelect; each line is one option). Skills are
**symlinked** from the repo, so edits to `<repo>/skills/` apply everywhere at once.

### Step 4 — Generate

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" create --name "<name>" --purpose "<purpose>" --repo "<path>" \
  --target ~/.claude --target ~/.claude-personal
# adopting: add  --wiki-dir llmwiki --raw-dir docs   (from scan)
# external: add  --home ~/lobes/_adopted/<name>
```

Run it once per lobe. It writes `wiki_tool.py`, `wikictl`, `LOBE.md` and
`skills/<name>-{add-raw,ingest,ask,lint}/SKILL.md` into the lobe home (the repo, or `--home`).
For a fresh lobe it also creates `raw/`, `wiki/index.md`, `wiki/log.md` and a `.gitignore`
entry. Re-running is safe: existing files are kept.

If it says the repo `already has a wiki at …`, it refused to create a second wiki beside an
existing one — go back to the scan and adopt it instead.

A `CONFLICT` line means a skill with that name already exists in that installation and was
left untouched — tell the user and offer a different lobe name.

### Step 5 — Commit, and optionally publish

**External lobes: skip this step** — nothing was written to the repo, and the lobe home is not
a git repo.

In place, show `git -C "<path>" status --short`, then commit only the files `create` listed:

```bash
git -C "<path>" add <files listed under Created> && git -C "<path>" commit -q -m "lobe: create <name> lobe"
```

For an adopted repo, ask before committing — it is an existing project.

If the repo has no remote, read `**Origin**:` in its `LOBE.md` first. `create` records where
the wiki content came from: `new`, a remote such as `github.com/<owner>/<repo>`, or what
`--origin` said. Offer a personal remote only when Origin is `new`, or a repo owned by your
login (`gh api user -q .login`). Otherwise the content belongs to someone else (often an
employer) — do **not** offer `gh repo create` under your account; say why, and suggest keeping
it local or a private repo in the owner's org. When you move adopted content into a new repo,
pass `--origin "<where it came from>"` so this check still works.

Only when the check allows it, ask whether to create a **private** GitHub repo. Only on a yes:

```bash
builtin cd "<path>" && gh repo create "<owner>/<repo-name>" --private --source . --push
```

Never make it public unless the user asks explicitly — a lobe holds personal data.

### Step 6 — Report

```
Lobe 'personal' ready at ~/lobes/personal
Skills: /personal-add-raw  /personal-ingest  /personal-ask  /personal-lint
Installed into: ~/.claude, ~/.claude-personal
Remote: github.com/<owner>/<repo> (private)  |  none

Restart Claude Code to load the new skills, then: /personal-add-raw <url or file>
```

## Install an existing lobe on another machine

In-place lobe: clone the repo, then link its skills (no files are generated):

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" install --repo "<path>" --target ~/.claude
```

External lobe: the home is not versioned — rerun `create` with the same name, `--repo`,
`--wiki-dir`, `--raw-dir` and `--home` to regenerate it.

## Upgrade an existing lobe

Lobes keep the tooling they were created with. After lobe-forge changes, re-render a lobe's
generated files (`wiki_tool.py`, `wikictl`, `LOBE.md`, `skills/<name>-*/SKILL.md`):

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" upgrade --repo "<lobe repo or home>"            # dry run
python3 "$FORGE" upgrade --repo "<lobe repo or home>" --apply    # on a yes
```

Show the dry run and ask before `--apply`. In place it refuses while those files have
uncommitted changes; afterwards show `git -C "<path>" diff --stat` and commit with the paths it
printed. An external home gets `*.bak` copies. If it reports new skills, run `install`.

## Uninstall or move a lobe

```bash
FORGE="$(builtin cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" uninstall --repo "<lobe repo or home>"            # remove its skill links everywhere
python3 "$FORGE" uninstall --repo "<lobe home>" --purge            # external: also delete the home
```

It removes only links that point into that lobe; `--target <dir>` limits it to one installation.
`--purge` works for external homes only, and refuses (deleting nothing) if the home holds files
forge did not generate. Wiki pages and sources are never touched. In place, the lobe files are
part of the repo: remove them with `git rm` if wanted.

To move a lobe (e.g. external → its own repo): move or copy the wiki, `uninstall --purge` the
old home, then `create` in the new repo with `--origin "<where the content came from>"`.
Confirm with the user before `--purge`.
