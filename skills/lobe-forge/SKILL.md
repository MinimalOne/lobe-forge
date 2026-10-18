---
name: lobe-forge
description: |
  Create a new lobe — a git-backed LLM wiki for one purpose (personal, family, career, an
  idea) — with its own named skills: <name>-add-raw, <name>-ingest, <name>-ask, <name>-lint.
  Asks for the lobe name, its purpose, whether to create a new repo or use an existing one, and
  which Claude installations to install into. Also re-installs an existing lobe on a new machine.
  Trigger phrases: "/lobe-forge", "create a lobe", "new lobe", "forge a lobe",
  "new second brain wiki", "create wiki skills for", "install my lobe".
version: 1.0.0
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

### Step 2 — Repo

Ask: **create a new repo** or **use an existing one**?

- **New**: ask for the local path. Suggest `~/lobes/<name>`. `forge.py` creates the folder and
  runs `git init`.
- **Existing, already cloned**: ask for its local path.
- **Existing, on GitHub only**: ask for the URL and a local path, then clone it:
  `git clone <url> <path>`.

`forge.py` refuses a repo that already holds a different lobe, or has a `wiki/` folder that
is not a lobe wiki — then ask for another path.

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
```

It creates `raw/`, `wiki/index.md`, `wiki/log.md`, `wiki_tool.py`, `LOBE.md`,
`skills/<name>-{add-raw,ingest,ask,lint}/SKILL.md` and a `.gitignore` entry, then links the
skills. Re-running it is safe: existing files are kept.

A `CONFLICT` line means a skill with that name already exists in that installation and was
left untouched — tell the user and offer a different lobe name.

### Step 5 — Commit, and optionally publish

Commit the scaffold with explicit paths:

```bash
cd "<path>" && git add raw/.gitkeep wiki/index.md wiki/log.md wiki_tool.py LOBE.md skills .gitignore && \
  git commit -q -m "lobe: create <name> lobe"
```

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

Clone the lobe repo, then link its skills (no files are generated):

```bash
FORGE="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/scripts/forge.py"
python3 "$FORGE" install --repo "<path>" --target ~/.claude
```
