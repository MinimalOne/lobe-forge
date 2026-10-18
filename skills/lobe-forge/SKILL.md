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
FORGE="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
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
FORGE="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" scan "<path>"
```

Each hit prints `wiki_dir`, `raw_dir`, page count, how many pages are `legacy` (old body format,
no frontmatter), the git `remote`, and `managed_by` (an existing `*-ask` skill or an
`adopted:<name>` home).

- **No hits** → a fresh lobe in that repo (Step 3, default `wiki/` + `raw/`).
- **Hits** → ask which to adopt (`AskUserQuestion`, multiSelect, one option per wiki: path,
  pages, managed_by), plus "none — create a fresh lobe". Recommend **not** adopting a wiki whose
  `managed_by` is not `-`: it already has skills, and a second set would compete with them.
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
convert them.

### Step 3 — Where to install

List the Claude installations on this machine:

```bash
FORGE="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" targets
```

Ask which to install into (`AskUserQuestion`, multiSelect; each line is one option). Skills are
**symlinked** from the repo, so edits to `<repo>/skills/` apply everywhere at once.

### Step 4 — Generate

```bash
FORGE="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
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
cd "<path>" && git add <files listed under Created> && git commit -q -m "lobe: create <name> lobe"
```

For an adopted repo, ask before committing — it is an existing project.

If the repo has no remote, ask whether to create a **private** GitHub repo. Only on a yes:

```bash
cd "<path>" && gh repo create "<owner>/<repo-name>" --private --source . --push
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
FORGE="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" install --repo "<path>" --target ~/.claude
```

External lobe: the home is not versioned — rerun `create` with the same name, `--repo`,
`--wiki-dir`, `--raw-dir` and `--home` to regenerate it.
