---
name: {{NAME}}-ask
description: |
  Answer questions from the remote {{NAME}} lobe wiki on lobe-server ({{PURPOSE}}).
  Searches the wiki via MCP, reads only the best-matching pages, answers with citations,
  and offers to save valuable answers as pages.
  Trigger phrases: "/{{NAME}}-ask", "ask {{NAME}}", "what does {{NAME}} say about",
  "check my {{NAME}} wiki", "search {{NAME}}".
version: 1.0.0
---

# {{NAME}}-ask (Remote MCP)

Answer questions using the **{{NAME}}** lobe on `lobe-server` via MCP. Purpose: {{PURPOSE}}

## Token Budget

Never read `{{WIKI}}/index.md` or `{{WIKI}}/log.md` whole. Read at most **5 pages** per question via `lobe_read_page`.

## Workflow

### Step 1 — Search via MCP

Split the question into 2–4 key concepts. Pass one string per concept in `terms`, using regex alternation (`|`) for synonyms and abbreviations:

```json
{
  "lobe": "{{NAME}}",
  "terms": ["concept1|synonym1", "concept2|synonym2"],
  "limit": 5
}
```

Call **`lobe_search`**. If no hits are returned, retry once with broader terms or call **`lobe_sections`** (`{"lobe": "{{NAME}}"}`).

### Step 2 — Read the best pages via MCP

For the 1–3 best hits whose `summary`, `atom:`, or `match:` snippet addresses the question, call **`lobe_read_page`**:

```json
{
  "lobe": "{{NAME}}",
  "slug": "<page-slug>"
}
```

### Step 3 — Follow links only when needed

Call **`lobe_links`** (`{"lobe": "{{NAME}}", "page": "<page-slug>"}`) to see incoming and outgoing `[[wikilinks]]` with summaries. Open a linked page with **`lobe_read_page`** only if its summary indicates it holds a missing fact.

### Step 4 — Answer

- Cite pages inline `(see [[page]])` and sources for specific facts `(source: file)`.
- Every factual sentence must trace to a page you read.
- If pages disagree, state both with their `last_updated` dates.
- If the wiki does not cover the question, say `"The {{NAME}} wiki does not cover this yet."` and suggest `/{{NAME}}-add-raw`.

### Step 5 — Offer to save

If the answer synthesizes several pages or will be needed again, offer to save it as `{{WIKI}}/<slug>.md` using **`lobe_sections`** + **`lobe_write_page`** (with `section`, `description`, and `log_entry`).

## Output

```text
Answer (from {{NAME}} wiki on lobe-server):

<answer with citations>

Sources consulted: [[page-1]], [[page-2]]
```
