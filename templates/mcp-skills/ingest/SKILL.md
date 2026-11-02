---
name: {{NAME}}-ingest
description: |
  Ingest a raw source into the remote {{NAME}} lobe wiki on lobe-server ({{PURPOSE}}).
  Reads the source via MCP, finds related pages by search, agrees a plan with the user, then
  creates or updates pages, links, index and log, lints, and commits on lobe-server.
  Trigger phrases: "/{{NAME}}-ingest", "ingest into {{NAME}}", "process {{NAME}} source",
  "add this to my {{NAME}} wiki", "update {{NAME}} wiki from".
version: 1.0.0
---

# {{NAME}}-ingest (Remote MCP)

Ingest a raw source into the **{{NAME}}** lobe on `lobe-server` via MCP. Purpose: {{PURPOSE}}

> **Rules**: Never modify `{{RAW}}/`. Never read `{{WIKI}}/index.md` or `{{WIKI}}/log.md` whole — use `lobe_search`, `lobe_sections`, and the `section`, `description`, and `log_entry` fields of `lobe_write_page` (`index_section`, `index_description`, and `log_entry` on `lobe_edit_page`). Never rewrite an existing page with `lobe_write_page`; change it with `lobe_edit_page`.

## Page Format

```markdown
---
title: Page Title
type: Note
summary: One sentence (15-40 words) saying what this page is.
aliases:
  - Other Name
atoms:
  - "cmd: exact command that worked"
  - "fix: exact error message -> how it was fixed"
  - "decision: chose X over Y because Z"
sources:
  - {{RAW}}/source-file.md
source_count: 1
provenance:
  - "Conversation, YYYY-MM-DD"
last_updated: YYYY-MM-DD
---

# Page Title

Content with [[wiki-links]] and (source: source-file.md) after factual claims.

## Related pages

- [[related-page]]
```

## Workflow

### Step 1 — Read the source

- If no source filename was given, call **`lobe_pending`** (`{"lobe": "{{NAME}}"}`) to list raw sources that no wiki page cites yet, then ask the user which one to ingest.
- Call **`lobe_read_raw`** (`{"lobe": "{{NAME}}", "filename": "<file>"}`) to read the raw source in full from `lobe-server`. (If the user provided new text or a local file not yet in `{{RAW}}/`, save it first with **`lobe_add_raw`**.)

### Step 2 — Find existing pages (mandatory)

1. **Candidates**: List the entities and concepts the source substantively discusses, along with aliases.
2. **Batch search via MCP**: Call **`lobe_search`**:
   ```json
   {
     "lobe": "{{NAME}}",
     "terms": ["entity1|alias1", "concept2|alias2"],
     "limit": 5
   }
   ```
3. **Read matched pages**: Call **`lobe_read_page`** (`{"lobe": "{{NAME}}", "slug": "<page>"}`) for every matched page, and **`lobe_links`** (`{"lobe": "{{NAME}}", "page": "<page>"}`) to inspect neighbors. Keep each page's returned `sha`. For a page over 120 lines, read the frontmatter (`"start_line": 1, "end_line": 20`); the response lists the page's `sections`, so then read only the ones the source touches (`"section": "<Section>"`).

### Step 3 — Plan, then wait

Present:
- What the source is (1–2 sentences)
- 5–10 key takeaways
- Pages to create and pages to update (with summary of changes). A page already over 200 lines (`lines` from `lobe_read_page`, or `lines=` in search hits): propose splitting it into a hub plus sub-pages instead of growing it.
- Any conflicts between existing claims and new claims

End with "Does this look right?" and wait for confirmation (unless the user explicitly asked not to wait for confirmation).

### Step 4 — Write pages, index, and log via MCP

1. Call **`lobe_sections`** (`{"lobe": "{{NAME}}"}`) to list the `## <Section>` headings in `{{WIKI}}/index.md`.
2. For each **new page**, call **`lobe_write_page`** with `section` (a section from `lobe_sections`) and `description` (≤25 words) so `lobe-server` registers the page in `{{WIKI}}/index.md`.
3. For each **existing page**, call **`lobe_edit_page`** once with all its changes in `edits` and the `sha` from `lobe_read_page` as `expected_sha`. Send only the changed text, never the whole page:
   ```json
   {
     "lobe": "{{NAME}}",
     "slug": "<page>",
     "expected_sha": "<sha>",
     "edits": [
       {"op": "find_replace", "find": "source_count: 1", "content": "source_count: 2"},
       {"op": "find_replace", "find": "last_updated: <old>", "content": "last_updated: <today>"},
       {"op": "find_replace", "find": "  - {{RAW}}/<last-source>\n", "content": "  - {{RAW}}/<last-source>\n  - {{RAW}}/<file>\n"},
       {"op": "append", "section": "<Section>", "content": "<new facts with (source: <file>)>"}
     ]
   }
   ```
   - Ops: `find_replace` (exact text, must match `expected_count` times, default 1), `append` (end of the page, or of `section`), `replace_section` (replace the body under a `## ` heading). The edits apply in order, all or nothing, in one commit.
   - Also add new `atoms:` lines and record superseded claims under `## Contradictions / Updates`. Never drop existing sections or sources.
   - If it fails with "changed since it was read", read the page again and rebuild the edits. If a `find` matches 0 or several times, make the `find` text longer and unique.
4. On the final `lobe_write_page` or `lobe_edit_page` call of the ingest, pass `log_entry` to append the operation log entry to `{{WIKI}}/log.md`:
     ```markdown
     ## <YYYY-MM-DD> — Ingest: <source-file>

     - Created: <pages>
     - Updated: <pages>
     - Source: `{{RAW}}/<file>`
     ```

### Step 5 — Lint via MCP

Call **`lobe_lint`** (`{"lobe": "{{NAME}}", "strict": true}`). If any hard errors are reported (broken links, frontmatter, missing index entry, or `source_count` mismatch), fix them with **`lobe_edit_page`** (pass `index_section` + `index_description` for a missing index entry) and re-run **`lobe_lint`** until `returncode` is `0`.

## Output

```text
Ingested (remote MCP): {{RAW}}/<file>
Created (N): {{WIKI}}/a.md, …
Updated (M): {{WIKI}}/b.md, …
Lint: clean · Committed on lobe-server: <hash>
```
