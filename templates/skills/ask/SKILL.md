---
name: {{NAME}}-ask
description: |
  Answer questions from the {{NAME}} lobe wiki ({{PURPOSE}}). Searches the wiki, reads only
  the best-matching pages, answers with citations, and offers to save valuable answers as pages.
  Trigger phrases: "/{{NAME}}-ask", "ask {{NAME}}", "what does {{NAME}} say about",
  "check my {{NAME}} wiki", "search {{NAME}}".
version: 1.0.0
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
---

# {{NAME}}-ask

Answer questions using the **{{NAME}}** lobe. Purpose: {{PURPOSE}}

## Paths

`SKILL_BASE_DIR` is the path shown in the `Base directory for this skill:` header. Shell
variables do not persist between Bash calls — start every Bash command with:

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd); TOOL="$REPO/wiki_tool.py"
```

| Command | Purpose |
|---|---|
| `python3 "$TOOL" search <term> [<term> …]` | Ranked pages with summary + snippet |
| `python3 "$TOOL" links <page>` | Outgoing links with summaries, and inbound links |
| `python3 "$TOOL" sections` / `index-add` | Index sections / insert an index row |

## Token budget

Never read `wiki/index.md` or `wiki/log.md` whole. Read at most **5 pages** per question; if
that is not enough, say what is missing rather than reading more.

## Workflow

### Step 1 — Search

Split the question into 2–4 key concepts. Pass **one term per concept**, with synonyms,
nicknames and spelling variants as regex alternation:

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd); TOOL="$REPO/wiki_tool.py"
python3 "$TOOL" search "school|kindergarten|kita" "fees?|cost|price"
```

Pages matching more terms rank higher; title, slug and alias matches outrank body matches.
The summary and snippet per hit are often enough to rule a page in or out unopened.

No hits → retry once with broader terms. Still none → `python3 "$TOOL" sections`, then
`grep -n -A40 '^## <Section>' "$REPO/wiki/index.md"`.

### Step 2 — Read the best pages

Read the hits whose summary or snippet addresses the question (usually 2–3). For a page
over 200 lines, grep for the relevant heading and read only that section with `offset`/`limit`.

### Step 3 — Follow links only when needed

`python3 "$TOOL" links <page>` — open a linked page only if its summary says it holds a fact
the answer still lacks.

### Step 4 — Answer

- Cite pages inline `(see [[page]])` and sources for specific facts `(source: file)`.
- Every factual sentence must trace to a page you read — not a snippet, not background knowledge.
- Keep claims scoped as the page states them (who, when, where).
- If pages disagree, give both with their `last_updated` dates.
- Partial coverage → answer that part and name the gap. No coverage → "The {{NAME}} wiki
  does not cover this yet." and suggest `/{{NAME}}-add-raw`.

### Step 5 — Offer to save

If the answer combines several pages or will recur, offer to save it as
`wiki/<name>.md` (format in `$REPO/LOBE.md`; put "Synthesized from wiki pages: a, b" in
`provenance`). Then, without reading the index or log:

```bash
REPO=$(cd -P "${SKILL_BASE_DIR}/../.." && pwd); TOOL="$REPO/wiki_tool.py"
python3 "$TOOL" index-add --section "<Section>" --page <name> --description "<≤25 words>"
printf '\n## %s — Ask: %s\n\n- Created: %s\n' "$(date +%F)" "<question, short>" "<name>" >> "$REPO/wiki/log.md"
git -C "$REPO" add wiki/<name>.md wiki/index.md wiki/log.md && git -C "$REPO" commit -q -m "wiki: save answer <name>"
```

## Output

```
Answer (from {{NAME}} wiki):

<answer with citations>

Sources consulted: [[page-1]], [[page-2]]
```
