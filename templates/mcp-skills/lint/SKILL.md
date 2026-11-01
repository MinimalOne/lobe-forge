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
| J | Long page — over 200 lines; split into a hub page plus sub-pages |
| K | Likely duplicate — two pages share half their title, alias, summary and atom words; merge, or link and narrow each |

## Step 2 — Report findings

```text
## {{NAME}} Wiki Lint (lobe-server) — <YYYY-MM-DD>

### G. Frontmatter (N)
### C. Broken links (N)
### B. Orphans (N)
### D. Index (N)
### I. Missing atoms (N)
### J. Long pages (N)
### K. Likely duplicates (N)
### Pending raw sources (N)   ← from lobe_pending; offer /{{NAME}}-ingest

Total: N — fix G, C, H first, then B, D, and I; J and K when the user wants a cleanup.
```

## Step 3 — Offer and apply fixes via MCP

Ask the user which issues to fix, then:
1. Read only the flagged pages using **`lobe_read_page`** (`{"lobe": "{{NAME}}", "slug": "<page>"}`).
2. Fix frontmatter (`source_count`, `sources`, `atoms`), broken `[[links]]`, orphan backlinks, or missing index rows using **`lobe_edit_page`** with only the changed text (`{"lobe": "{{NAME}}", "slug": "<page>", "expected_sha": "<sha from lobe_read_page>", "edits": [{"op": "find_replace", "find": "...", "content": "..."}]}`). For a missing index row, also pass `index_section` and `index_description`.
3. **Long pages (J)**, one per run and only on a yes: read the page's `sections` from a partial **`lobe_read_page`** (`"start_line": 1, "end_line": 20`), propose 2–5 sub-pages, one per group of sections, and keep the original as a short hub (title, summary, whole-topic atoms, one line plus a `[[link]]` per sub-page). Create sub-pages with **`lobe_write_page`** (own frontmatter and sources, plus `section` and `description`), then shrink the hub with **`lobe_edit_page`** `replace_section` edits.
4. **Likely duplicates (K)**: read both pages' frontmatter. Same subject: merge the smaller into the larger (union of sources and atoms) and replace the smaller one's body with a one-line pointer. Only overlapping: add `[[links]]` both ways and narrow each summary.
5. Re-run **`lobe_lint`** (`{"lobe": "{{NAME}}", "strict": true}`) to confirm zero hard errors.
