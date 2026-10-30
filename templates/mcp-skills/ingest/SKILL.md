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

> **Rules**: Never modify `{{RAW}}/`. Never read `{{WIKI}}/index.md` or `{{WIKI}}/log.md` whole — use `lobe_search`, `lobe_sections`, and the `section`, `description`, and `log_entry` fields of `lobe_write_page`.

## Page Format

```markdown
---
title: Page Title
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
3. **Read matched pages**: Call **`lobe_read_page`** (`{"lobe": "{{NAME}}", "slug": "<page>"}`) for every matched page, and **`lobe_links`** (`{"lobe": "{{NAME}}", "page": "<page>"}`) to inspect neighbors.

### Step 3 — Plan, then wait

Present:
- What the source is (1–2 sentences)
- 5–10 key takeaways
- Pages to create and pages to update (with summary of changes)
- Any conflicts between existing claims and new claims

End with "Does this look right?" and wait for confirmation (unless the user explicitly asked not to wait for confirmation).

### Step 4 — Write pages, index, and log via MCP

1. Call **`lobe_sections`** (`{"lobe": "{{NAME}}"}`) to list the `## <Section>` headings in `{{WIKI}}/index.md`.
2. For each new or updated page, call **`lobe_write_page`**:
   - For **new pages**, pass `section` (a section from `lobe_sections`) and `description` (≤25 words) so `lobe-server` registers the page in `{{WIKI}}/index.md`.
   - For **existing pages**, merge changes into the content returned by `lobe_read_page` (never drop existing sections or sources; append `{{RAW}}/<file>` to `sources:`, increment `source_count`, update `atoms:` and `last_updated`, and record superseded claims under `## Contradictions / Updates`).
   - On the final `lobe_write_page` call of the ingest, pass `log_entry` to append the operation log entry to `{{WIKI}}/log.md`:
     ```markdown
     ## <YYYY-MM-DD> — Ingest: <source-file>

     - Created: <pages>
     - Updated: <pages>
     - Source: `{{RAW}}/<file>`
     ```

### Step 5 — Lint via MCP

Call **`lobe_lint`** (`{"lobe": "{{NAME}}", "strict": true}`). If any hard errors are reported (broken links, frontmatter, missing index entry, or `source_count` mismatch), fix them with **`lobe_write_page`** and re-run **`lobe_lint`** until `returncode` is `0`.

## Output

```text
Ingested (remote MCP): {{RAW}}/<file>
Created (N): {{WIKI}}/a.md, …
Updated (M): {{WIKI}}/b.md, …
Lint: clean · Committed on lobe-server: <hash>
```
