---
name: {{NAME}}-lint
description: |
  Audit the remote {{NAME}} lobe wiki on lobe-server ({{PURPOSE}}) via MCP: frontmatter,
  orphan pages, broken links, index drift, missing atoms, and pending raw sources.
  Reports a numbered list and offers fixes.
  Trigger phrases: "/{{NAME}}-lint", "lint {{NAME}}", "audit my {{NAME}} wiki",
  "{{NAME}} wiki health check".
version: 1.0.0
---

# {{NAME}}-lint (Remote MCP)

Audit the **{{NAME}}** lobe on `lobe-server` via MCP. Purpose: {{PURPOSE}}

## Step 1 — Run deterministic checks via MCP

Call two MCP tools on `lobe-server`:
1. **`lobe_lint`**: `{"lobe": "{{NAME}}", "strict": false}`
2. **`lobe_pending`**: `{"lobe": "{{NAME}}"}`

| Check | Meaning |
|---|---|
| G | Frontmatter missing, `source_count` mismatch, empty title/summary/last_updated, no sources |
| B | Orphan page — no inbound `[[links]]` |
| C | Broken link — target page missing |
| D | Index drift — index row without page, or page not in index |
| H | `sources:` file missing from `{{RAW}}/` |
| I | Missing `atoms:` in frontmatter |

## Step 2 — Report findings

```text
## {{NAME}} Wiki Lint (lobe-server) — <YYYY-MM-DD>

### G. Frontmatter (N)
### C. Broken links (N)
### B. Orphans (N)
### D. Index (N)
### I. Missing atoms (N)
### Pending raw sources (N)   ← from lobe_pending; offer /{{NAME}}-ingest

Total: N — fix G, C, H first, then B, D, and I.
```

## Step 3 — Offer and apply fixes via MCP

Ask the user which issues to fix, then:
1. Read only the flagged pages using **`lobe_read_page`** (`{"lobe": "{{NAME}}", "slug": "<page>"}`).
2. Fix frontmatter (`source_count`, `sources`, `atoms`), broken `[[links]]`, orphan backlinks, or missing index rows using **`lobe_write_page`** (`{"lobe": "{{NAME}}", "slug": "<page>", "content": "...", "section": "...", "description": "..."}`).
3. Re-run **`lobe_lint`** (`{"lobe": "{{NAME}}", "strict": true}`) to confirm zero hard errors.
