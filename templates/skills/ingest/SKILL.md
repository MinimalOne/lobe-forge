---
name: {{NAME}}-ingest
description: |
  Ingest a raw source into the {{NAME}} lobe wiki ({{PURPOSE}}). Reads the source, finds
  related pages by search, agrees a plan with the user, then creates or updates pages, links,
  index and log, lints, and commits.
  Trigger phrases: "/{{NAME}}-ingest", "ingest into {{NAME}}", "process {{NAME}} source",
  "add this to my {{NAME}} wiki", "update {{NAME}} wiki from".
version: 1.0.0
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - AskUserQuestion
---

# {{NAME}}-ingest

Ingest a raw source into the **{{NAME}}** lobe. Purpose: {{PURPOSE}}

## Paths

`SKILL_BASE_DIR` is the path shown in the `Base directory for this skill:` header. Shell
variables do not persist between Bash calls — start every Bash command with:

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd); TOOL="$REPO/wiki_tool.py"
```

Page format and rules: `$REPO/LOBE.md` (read it once per session if unsure).

> **Rules**: never modify `raw/`. Never read `wiki/index.md` or `wiki/log.md` whole — use
> `search`, `index-add` and `>>`.

## Inputs

- **Source**: a path in `raw/` (if missing, list `raw/` and ask with `AskUserQuestion`).
  Markdown, text, PDF, images and docs are all read with `Read`; read large files in sections.

## Workflow

### Step 0 — Clean tree

`git -C "$REPO" status --short`. If anything is modified or untracked, ask whether to commit it
first, include it, or leave it. Never sweep it into the ingest commit.

### Step 1 — Read the source

Read it in full. For images, describe what is shown and transcribe any text.

### Step 2 — Find the existing pages it touches (mandatory)

1. **Candidates**: list the entities (people, places, organisations, things, projects) and
   concepts (ideas, decisions, procedures) the source **substantively discusses** — a
   paragraph, several bullets, or concrete facts, dates, numbers. Skip passing mentions.
   Note each candidate's slug and aliases (nicknames, abbreviations, other spellings).
2. **Search once per candidate**, names and aliases as alternation:
   ```bash
   REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd); TOOL="$REPO/wiki_tool.py"
   python3 "$TOOL" search "grandma|oma|maria" --limit 5
   ```
3. **Same, not related**: a candidate maps to an existing page only if both are the **same**
   thing. Different people, years, versions or sibling projects are separate pages. In doubt,
   don't merge — propose a new page that links to the related one.
4. **Read every matched page in full.** `python3 "$TOOL" links <page>` shows neighbours with
   summaries without opening them.

### Step 3 — Plan, then wait

Present: what the source is (1–2 sentences), 5–10 key takeaways, pages to create, pages to
update (and what changes), and every **conflict** (current claim vs new claim — ask which is
right, or whether to record both). End with "Does this look right?" and wait.

### Step 4 — Write pages

- **Summary page** `wiki/<source-slug>.md` and **concept pages**: format from `LOBE.md`;
  `sources: [raw/<file>]`, `source_count: 1`, `last_updated: today`, 15–40-word `summary`.
- **Existing pages**: targeted `Edit` calls only, never a full rewrite or removal. In the same
  edit append the source to `sources:`, bump `source_count`, set `last_updated`.
- **Conflicts**: apply the user's decision; when the source supersedes a claim, update it and
  add a dated line under `## Contradictions / Updates`.
- Create a page only for what the source substantively discusses; a one-sentence page belongs
  as a line on an existing page. Fewer, denser pages.

**You are a compiler, not a creative writer**: every new claim is stated in the source; stay
close to its wording; no filler; keep claims scoped (who said it, when, where); cite
`(source: <file>)` after facts; link only `[[pages]]` that exist or that you create now.

### Step 5 — Links

- Summary page ↔ each concept page; concept pages → related pages.
- Pages that mention a new concept without linking it, found by grep, not by reading:
  ```bash
  REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)
  cd "$REPO/wiki" && grep -liE 'name|alias' *.md | grep -v -e '^index.md$' -e '^log.md$' -e '^<page>.md$' \
    | xargs grep -L '\[\[<page>\]\]'
  ```
  `grep -n` each hit, `Read` only that region, `Edit` the mention into `[[<page>]]`.

### Step 6 — Index, log, lint, commit

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd); TOOL="$REPO/wiki_tool.py"
python3 "$TOOL" sections
python3 "$TOOL" index-add --section "<Section>" --page <page> --description "<≤25 words>"   # per new page
cat >> "$REPO/wiki/log.md" <<'LOG'

## <YYYY-MM-DD> — Ingest: <source-file>

- Created: <pages>
- Updated: <pages>
- Source: `raw/<file>`
LOG
python3 "$TOOL" lint --strict
```

No fitting section → ask the user; add it as a new `## <Section>` heading in `index.md`.
Fix any hard lint error (broken link, frontmatter, missing source) before committing.

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)
git -C "$REPO" add <each page created or edited> wiki/index.md wiki/log.md && \
  git -C "$REPO" commit -q -m "wiki: ingest <source-slug> — <N> created, <M> updated"
```

Stage explicit paths only. Then ask whether to `git push`.

## Output

```
Ingested: raw/<file>
Created (N): wiki/a.md, …
Updated (M): wiki/b.md, …
Lint: clean · Committed: <hash>
```
