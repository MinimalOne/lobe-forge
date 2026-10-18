---
name: {{NAME}}-add-raw
description: |
  Add a new source to the {{NAME}} lobe's raw/ folder ({{PURPOSE}}). Saves a URL's content,
  copies a local file (PDF, image, markdown, doc), or saves pasted or typed text.
  Trigger phrases: "/{{NAME}}-add-raw", "add to {{NAME}}", "save this to {{NAME}}",
  "add {{NAME}} source", "new {{NAME}} doc".
version: 1.0.0
allowed-tools:
  - Bash
  - Read
  - Write
  - WebFetch
  - AskUserQuestion
---

# {{NAME}}-add-raw

Add a source document to the **{{NAME}}** lobe. Purpose: {{PURPOSE}}

## Paths

`SKILL_BASE_DIR` is the path shown in the `Base directory for this skill:` header. Shell
variables do not persist between Bash calls — start every Bash command with:

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)   # repo root, resolved through the install symlink
```

> **Rule**: `raw/` is append-only. Never modify or delete a file in it.

## Workflow

### Step 1 — Pick the mode

From context, or ask with `AskUserQuestion`:

1. **URL** — fetch the page with `WebFetch` (prompt: "Return the full main content as clean
   markdown, keeping headings, lists, tables and numbers; drop navigation and ads") and save
   it with a header block (see Step 2).
2. **Local file** — copy it as-is with `cp`. PDFs, images and Office docs stay in their
   original format; `{{NAME}}-ingest` reads them directly.
3. **Pasted or typed text** — save it as markdown.

### Step 2 — Name and write

- Filename: `kebab-case`, descriptive, with a date prefix when the content is time-bound
  (e.g. `2026-10-18-school-meeting-notes.md`). Optional subfolder by topic if one already exists
  (`ls "$REPO/raw"`).
- If the destination exists, ask before choosing another name — never overwrite.
- URL and text sources start with:

```markdown
---
source: <URL, or "typed by user">
captured: <YYYY-MM-DD>
---
```

### Step 3 — Commit

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)
git -C "$REPO" add "raw/<file>" && git -C "$REPO" commit -q -m "raw: add <file>"
```

Stage only the new file.

## Output

```
Added: raw/<file>

Next: /{{NAME}}-ingest raw/<file>
```
