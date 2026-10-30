---
name: {{NAME}}-add-raw
description: |
  Add a new source to the remote {{NAME}} lobe's {{RAW}}/ folder on lobe-server ({{PURPOSE}}).
  Saves a URL's content, uploads a local file, or saves pasted or typed text via MCP.
  Trigger phrases: "/{{NAME}}-add-raw", "add to {{NAME}}", "save this to {{NAME}}",
  "add {{NAME}} source", "new {{NAME}} doc".
version: 1.0.0
---

# {{NAME}}-add-raw (Remote MCP)

Add a source document to the **{{NAME}}** lobe on `lobe-server` via MCP. Purpose: {{PURPOSE}}

> **Rule**: `{{RAW}}/` is append-only. Never overwrite or delete an existing source file.

## Workflow

### Step 1 — Pick the mode

From context, or ask the user:

1. **URL** — fetch the page content as clean markdown (keeping headings, lists, tables, and numbers; drop navigation and ads).
2. **Local file** — read the file from disk so its content can be sent to `lobe_add_raw`.
3. **Pasted or typed text** — format as markdown.

### Step 2 — Format the source

- Filename: `kebab-case.md`, descriptive, with a date prefix (`YYYY-MM-DD-topic-slug.md`).
- Prepend a YAML frontmatter block for URL and text sources:

```markdown
---
source: <URL, or "typed by user">
captured: <YYYY-MM-DD>
---
```

### Step 3 — Save on `lobe-server` via MCP

Call the **`lobe_add_raw`** MCP tool on `lobe-server`:

```json
{
  "lobe": "{{NAME}}",
  "filename": "<YYYY-MM-DD-slug.md>",
  "content": "<full markdown content>",
  "message": "raw: add <YYYY-MM-DD-slug.md>"
}
```

`lobe-server` writes `{{RAW}}/<filename>`, commits it to Git locally, and pushes to `origin` if auto-push is enabled.

## Output

```text
Added (remote MCP): {{RAW}}/<file> (commit <hash>)

Next: /{{NAME}}-ingest {{RAW}}/<file>
```
