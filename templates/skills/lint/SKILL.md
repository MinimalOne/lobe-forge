---
name: {{NAME}}-lint
description: |
  Audit the {{NAME}} lobe wiki ({{PURPOSE}}): frontmatter, orphan pages, broken links, index
  drift and missing sources by script, then outdated and contradicting pages by reasoning over
  script-selected candidates only. Reports a numbered list and offers fixes.
  Trigger phrases: "/{{NAME}}-lint", "lint {{NAME}}", "audit my {{NAME}} wiki",
  "{{NAME}} wiki health check".
version: 1.0.0
allowed-tools:
  - Bash
  - Read
  - Edit
---

# {{NAME}}-lint

Audit the **{{NAME}}** lobe. Purpose: {{PURPOSE}}

**Git**: {{COMMIT_RULE}}

## Paths

`SKILL_BASE_DIR` is the path shown in the `Base directory for this skill:` header. Shell
variables do not persist between Bash calls — start every Bash command with:

```bash
W="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/wikictl"; eval "$("$W" paths)"   # sets LOBE, REPO, WIKI, RAW
```

**Token rule**: never read every page. Read only pages the script flags or the candidate
queries below select.

## Step 1 — Deterministic checks

```bash
W="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/wikictl"; eval "$("$W" paths)"   # sets LOBE, REPO, WIKI, RAW
"$W" lint
```

| Check | Meaning |
|---|---|
| G | Frontmatter missing, `source_count` mismatch, empty title/summary/last_updated, no sources |
| B | Orphan page — no inbound `[[links]]` |
| C | Broken link — target page missing |
| D | Index drift — index row without page, or page not in index |
| H | `sources:` file missing from `{{RAW}}/` |

## Step 2 — Reasoning checks on candidates

```bash
W="$(cd -P "${SKILL_BASE_DIR}/../.." && pwd)/wikictl"; eval "$("$W" paths)"   # sets LOBE, REPO, WIKI, RAW
"$W" build >/dev/null && cd "$LOBE"   # catalog.jsonl lives in the lobe home
# E candidates: last_updated older than 6 months
python3 -c "import json,datetime as d;c=(d.date.today()-d.timedelta(days=183)).isoformat();[print(e['page'],e['last_updated']) for e in map(json.loads,open('catalog.jsonl')) if e['last_updated']<c]"
# F candidates: pages sharing a source
python3 -c "import json,collections as C;m=C.defaultdict(list);[m[s].append(e['page']) for e in map(json.loads,open('catalog.jsonl')) for s in e['sources']];[print(s,'->',' '.join(v)) for s,v in m.items() if len(v)>1]"
```

- **E — outdated**: flag a candidate if it states a price, version, status, plan or date-bound
  fact, or if a newer `{{RAW}}/` file covers the topic (`"$W" search <topic>`).
- **F — contradictions**: compare only pages in one F cluster or neighbours from
  `"$W" links <page>`. Compare summaries first; open both pages only when they make
  overlapping claims. Report "A says X, B says Y".
- If the user names a topic, `search` it and lint only those hits.

## Step 3 — Report

```
## {{NAME}} Wiki Lint — <date>

### G. Frontmatter (N)
1. {{WIKI}}/x.md — source_count=1 but 2 sources listed
### C. Broken links (N)
### B. Orphans (N)
### D. Index (N)
### E. Possibly outdated (N)
### F. Contradictions (N)

Total: N — fix G, C, H first, then B and D.
```

## Step 4 — Offer fixes

Targeted `Edit`s only: recount `source_count`, move non-file sources to `provenance`, add
missing index rows with `index-add`, add links to orphans from a related page. After fixing,
rerun `lint --strict`, then commit with explicit paths:
`git -C "$REPO" add <files> && git -C "$REPO" commit -q -m "wiki: lint fixes — <summary>"`.
