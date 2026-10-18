#!/usr/bin/env python3
"""
wiki_tool.py — deterministic CLI for a lobe (LLM wiki) repo.

Commands:
  build        Parse all wiki pages and write catalog.jsonl
  lint         Run deterministic quality checks (exits 0=ok, 1=issues found)
  source-scan  Show raw sources not yet ingested
  search       Rank pages for one or more terms (title/alias/summary/body), with snippets
  links        Show a page's outgoing links (with summaries) and inbound links
  sections     List index.md section headings
  index-add    Insert one row into an index.md section without reading the whole index

Paths default to this repo's layout (wiki/, raw/). Override with WIKI_ROOT, WIKI_DIR
and WIKI_RAW_DIR to point it at another layout.

Checks run by lint:
  G  YAML frontmatter — missing, source_count mismatch,      [data error]
     or neither sources nor provenance
  B  Orphan pages — no inbound wiki-links from any page      [quality]
  C  Broken links — [[target]] page does not exist           [error]
  D  Index discrepancy — file/index out of sync              [quality]
  H  Missing source files — sources: path does not exist     [data error]
"""

import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

REPO    = Path(os.environ.get("WIKI_ROOT") or Path(__file__).parent)
WIKI    = REPO / os.environ.get("WIKI_DIR", "wiki")
RAW     = REPO / os.environ.get("WIKI_RAW_DIR", "raw")
CATALOG = REPO / "catalog.jsonl"
MANIFEST = REPO / "source-manifest.jsonl"
INDEX   = WIKI / "index.md"
SKIP    = {"index.md", "log.md"}


# ── Parsing ───────────────────────────────────────────────────────────────────

def _unquote(val: str) -> str:
    """Strip YAML quotes; unescape \\" and \\\\ inside double quotes."""
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] == '"':
        return val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(val) >= 2 and val[0] == val[-1] == "'":
        return val[1:-1].replace("''", "'")
    return val


def _parse_fm_block(fm_text: str) -> dict:
    """Parse a YAML frontmatter block (no external deps)."""
    result: dict = {}
    lines = fm_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith("#"):
            i += 1
            continue
        if ":" in line and not line.startswith(" "):
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val == "[]":
                result[key] = []
                i += 1
            elif val:
                result[key] = _unquote(val)
                i += 1
            else:
                # Collect list items on following lines
                items = []
                i += 1
                while i < len(lines) and lines[i].startswith("  - "):
                    items.append(_unquote(lines[i][4:]))
                    i += 1
                result[key] = items
        else:
            i += 1
    return result


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter_dict, body) or (None, full_text) when absent."""
    if not text.startswith("---\n"):
        return None, text
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        return None, text
    fm = _parse_fm_block(text[4:end])
    body = text[end + 5:]
    return fm, body


def parse_legacy_fields(text: str) -> dict:
    """Extract metadata from legacy body fields (no frontmatter)."""
    out: dict = {}
    m = re.search(r"^# (.+)", text, re.MULTILINE)
    if m:
        out["title"] = m.group(1).strip()
    m = re.search(r"\*\*Summary\*\*:\s*(.+)", text)
    if m:
        out["summary"] = m.group(1).strip()
    m = re.search(r"\*\*Sources?\*\*:\s*(.+)", text)
    if m:
        raw = m.group(1).strip()
        parts = [p.strip().strip("`") for p in re.split(r",\s*", raw) if p.strip()]
        out["sources"] = parts
    m = re.search(r"\*\*Last updated\*\*:\s*(\S+)", text)
    if m:
        out["last_updated"] = m.group(1).strip()
    return out


def wiki_links_in(text: str) -> list[str]:
    # Strip pipe aliases ([[page|display]]) and anchors ([[page#section]])
    raw = re.findall(r"\[\[([^\]]+)\]\]", text)
    pages = []
    for r in raw:
        page = re.split(r"[|#]", r)[0].strip()
        if page:
            pages.append(page)
    return sorted(set(pages))


def parse_page(path: Path, text: str | None = None) -> dict:
    if text is None:
        text = path.read_text()
    fm, body = parse_frontmatter(text)

    if fm is not None:
        sources = fm.get("sources", [])
        if isinstance(sources, str):
            sources = [sources]
        provenance = fm.get("provenance", [])
        if isinstance(provenance, str):
            provenance = [provenance]
        try:
            sc = int(fm.get("source_count", -1))
        except (TypeError, ValueError):
            sc = -1
        aliases = fm.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        return {
            "page": f"{WIKI.name}/{path.name}",
            "title": fm.get("title", ""),
            "summary": fm.get("summary", ""),
            "sources": sources,
            "source_count": sc,
            "provenance": provenance,
            "aliases": aliases,
            "last_updated": fm.get("last_updated", ""),
            "wiki_links": wiki_links_in(text),
            "has_frontmatter": True,
        }

    fields = parse_legacy_fields(text)
    sources = fields.get("sources", [])
    return {
        "page": f"{WIKI.name}/{path.name}",
        "title": fields.get("title", ""),
        "summary": fields.get("summary", ""),
        "sources": sources,
        "source_count": len(sources),
        "provenance": [],
        "aliases": [],
        "last_updated": fields.get("last_updated", ""),
        "wiki_links": wiki_links_in(text),
        "has_frontmatter": False,
    }


# ── build ─────────────────────────────────────────────────────────────────────

def cmd_build(_args) -> int:
    pages = sorted(WIKI.glob("*.md"))
    entries = []
    for p in pages:
        if p.name in SKIP:
            continue
        entries.append(parse_page(p))

    with CATALOG.open("w") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    fm  = sum(1 for e in entries if e["has_frontmatter"])
    leg = len(entries) - fm
    print(f"catalog.jsonl — {len(entries)} pages  ({fm} with frontmatter, {leg} legacy)")
    return 0


# ── lint ──────────────────────────────────────────────────────────────────────

def cmd_lint(args) -> int:
    pages = sorted(WIKI.glob("*.md"))
    entries: dict[str, dict] = {}
    for p in pages:
        if p.name in SKIP:
            continue
        entries[p.name] = parse_page(p)

    page_names = set(entries.keys())
    issues: dict[str, list[tuple[str, str]]] = {c: [] for c in "GBCDH"}

    # G — Frontmatter
    legacy_pages = []
    for name, e in entries.items():
        if not e["has_frontmatter"]:
            legacy_pages.append(name)
            continue
        sc, actual = e["source_count"], len(e["sources"])
        if sc != actual:
            issues["G"].append((name, f"source_count={sc} declared but {actual} source(s) listed"))
        if not e["sources"] and not e["provenance"]:
            issues["G"].append((name, "no sources and no provenance listed"))
        for field in ("title", "summary", "last_updated"):
            if not e.get(field):
                issues["G"].append((name, f"frontmatter field '{field}' is empty or missing"))

    # B — Orphans: build inbound-link map (count index.md links too)
    inbound: dict[str, list[str]] = {n: [] for n in page_names}
    all_text_pages = [p for p in pages]  # include index and log for inbound counting
    for p in all_text_pages:
        src_name = p.name
        for link in wiki_links_in(p.read_text()):
            target = f"{link}.md"
            if target in inbound:
                inbound[target].append(src_name)
    for name in page_names:
        if not inbound[name]:
            issues["B"].append((name, "no inbound wiki-links from any other page"))

    # C — Broken links
    for name, e in entries.items():
        for link in e["wiki_links"]:
            target = f"{link}.md"
            if target not in page_names:
                issues["C"].append((name, f"[[{link}]] → {WIKI.name}/{link}.md does not exist"))

    # D — Index discrepancy
    index_text = INDEX.read_text()
    index_links = set(wiki_links_in(index_text))
    for link in index_links:
        if f"{link}.md" not in page_names:
            issues["D"].append(("index.md", f"[[{link}]] listed but {WIKI.name}/{link}.md missing"))
    for name in page_names:
        stem = name[:-3]
        if stem not in index_links:
            issues["D"].append((name, "file exists but not referenced in index.md"))

    # H — Source file existence (frontmatter pages only)
    for name, e in entries.items():
        if not e["has_frontmatter"]:
            continue
        for src in e["sources"]:
            if not (REPO / src).exists():
                issues["H"].append((name, f"source '{src}' not found on disk"))

    # ── Report ────────────────────────────────────────────────────────────────
    labels = {
        "G": "Frontmatter issues",
        "B": "Orphan pages (no inbound links)",
        "C": "Broken wiki-links",
        "D": "Index discrepancies",
        "H": "Missing source files",
    }
    # In strict mode only G-data-errors, C, and H are hard errors that block commits.
    # G-legacy (missing frontmatter), B, and D are soft warnings.
    strict = getattr(args, "strict", False)

    total = sum(len(v) for v in issues.values()) + len(legacy_pages)
    hard  = len(issues["C"]) + len(issues["G"]) + len(issues["H"])

    if total == 0:
        print("✓ No issues found.")
        return 0

    n = 1
    for check in "GBCDH":
        items = issues[check]
        soft_tag = "  [warning — not blocking]" if strict and check in ("B", "D") else ""

        if check == "G":
            leg_count = len(legacy_pages)
            fm_issues = len(items)
            header_count = fm_issues + leg_count
            if header_count == 0:
                continue
            leg_tag = "  [warning — not blocking]" if strict else ""
            print(f"\nG. {labels['G']} ({header_count})")
            if leg_count:
                print(f"  Legacy pages (no frontmatter): {leg_count}{leg_tag}")
                if args.show_legacy:
                    for pg in sorted(legacy_pages):
                        print(f"    - {pg}")
                else:
                    print(f"    (run with --show-legacy to list all {leg_count})")
            for page, msg in items:
                print(f"  {n}. {page} — {msg}")
                n += 1
        elif items:
            print(f"\n{check}. {labels[check]} ({len(items)}){soft_tag}")
            for page, msg in items:
                print(f"  {n}. {page} — {msg}")
                n += 1

    if strict:
        if hard == 0:
            print(f"\n✓ Strict mode: 0 hard errors (commit allowed). {total} soft warning(s) above.")
            return 0
        else:
            print(f"\n✗ Strict mode: {hard} hard error(s) — commit blocked.")
            return 1

    print(f"\nTotal: {total} issue(s)")
    return 1


# ── source-scan ───────────────────────────────────────────────────────────────

def cmd_source_scan(args) -> int:
    if not MANIFEST.exists():
        print("source-manifest.jsonl not found.", file=sys.stderr)
        return 1

    rows = [json.loads(l) for l in MANIFEST.read_text().splitlines() if l.strip()]
    today = datetime.date.today()

    display = rows if args.all else [r for r in rows if not r["ingested"]]

    if not display:
        print("All sources have been ingested.")
        return 0

    col_w = max(len(r["path"]) for r in display) + 2
    print(f"  {'STATUS':<10} {'DAYS':>5}  PATH")
    print(f"  {'-'*10} {'-'*5}  {'-'*(col_w)}")
    for r in display:
        status = "ingested" if r["ingested"] else "PENDING"
        added  = r.get("added") or ""
        try:
            age = (today - datetime.date.fromisoformat(added)).days
            age_s = f"{age}d"
        except ValueError:
            age_s = "?"
        print(f"  {status:<10} {age_s:>5}  {r['path']}")

    pending = sum(1 for r in rows if not r["ingested"])
    if not args.all:
        print(f"\n  Pending: {pending} of {len(rows)} sources")
    return 0


# ── search / links / sections / index-add ─────────────────────────────────────

def _compile(term: str) -> re.Pattern:
    try:
        return re.compile(term, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(term), re.IGNORECASE)


def _one_line(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _content_pages() -> list[Path]:
    return [p for p in sorted(WIKI.glob("*.md")) if p.name not in SKIP]


def cmd_search(args) -> int:
    terms = [_compile(t) for t in args.terms]
    hits = []
    for p in _content_pages():
        text = p.read_text()
        e = parse_page(p, text)
        _, body = parse_frontmatter(text)
        body = re.sub(r"\A\s*# .*\n", "", body)  # H1 repeats the title; keep snippets informative
        score, matched, snippet = 0, 0, ""
        for rx in terms:
            s = 0
            if rx.search(p.stem):
                s += 10
            if rx.search(e["title"]):
                s += 8
            if any(rx.search(a) for a in e["aliases"]):
                s += 8
            if rx.search(e["summary"]):
                s += 4
            found = rx.findall(body)
            s += min(len(found), 5)
            if s:
                matched += 1
                score += s
                if not snippet and found:
                    m = rx.search(body)
                    snippet = _one_line(body[max(0, m.start() - 70): m.end() + 70], 170)
        if score:
            # pages matching more distinct terms outrank pages that repeat one term
            hits.append((score + 5 * matched, p.stem, e, snippet))

    if not hits:
        print("No matching pages.")
        return 1
    hits.sort(key=lambda h: (-h[0], h[1]))
    for score, slug, e, snippet in hits[: args.limit]:
        print(f"[[{slug}]] score={score} updated={e['last_updated'] or '?'} — {e['title']}")
        if e["summary"]:
            print(f"    summary: {_one_line(e['summary'], 200)}")
        if snippet:
            print(f"    match:   {snippet}")
    print(f"\n{min(len(hits), args.limit)} of {len(hits)} matching page(s) shown")
    return 0


def cmd_links(args) -> int:
    entries = {p.stem: parse_page(p) for p in _content_pages()}
    slug = args.page.removesuffix(".md").removeprefix(f"{WIKI.name}/")
    if slug not in entries:
        print(f"No page '{slug}'. Try: wiki_tool.py search {slug}", file=sys.stderr)
        return 1
    e = entries[slug]
    print(f"[[{slug}]] — {e['title']} (updated {e['last_updated'] or '?'})")
    print(f"  summary: {_one_line(e['summary'], 300)}")
    out = [l for l in e["wiki_links"] if l != slug]
    print(f"\nLinks out ({len(out)}):")
    for l in out:
        target = entries.get(l)
        desc = _one_line(target["summary"], 150) if target else "(page missing)"
        print(f"  [[{l}]] — {desc}")
    inbound = sorted(s for s, en in entries.items() if s != slug and slug in en["wiki_links"])
    print(f"\nLinked from ({len(inbound)}): " + ", ".join(f"[[{s}]]" for s in inbound))
    return 0


def _headings(lines: list[str]) -> list[tuple[int, str]]:
    return [(i, l.lstrip("#").strip()) for i, l in enumerate(lines) if l.startswith("## ")]


def cmd_sections(_args) -> int:
    for _, h in _headings(INDEX.read_text().splitlines()):
        print(h)
    return 0


def cmd_index_add(args) -> int:
    lines = INDEX.read_text().splitlines()
    page = args.page.removesuffix(".md")
    if any(f"[[{page}]]" in l for l in lines):
        print(f"[[{page}]] already in index.md — nothing to do.")
        return 0
    heads = _headings(lines)
    match = [i for i, h in heads if h.lower() == args.section.lower()]
    if not match:
        print(f"Section '{args.section}' not found. Sections:", file=sys.stderr)
        for _, h in heads:
            print(f"  {h}", file=sys.stderr)
        return 1
    start = match[0]
    end = next((i for i, _ in heads if i > start), len(lines))
    rows = [i for i in range(start + 1, end) if lines[i].startswith("|")]
    desc = " ".join(args.description.replace("|", "/").split())
    row = f"| [[{page}]] | {desc} |"
    if rows:
        lines.insert(rows[-1] + 1, row)
    else:
        lines[start + 1:start + 1] = ["", "| Page | Description |", "|---|---|", row]
    INDEX.write_text("\n".join(lines) + "\n")
    print(f"Added [[{page}]] to '{heads[[i for i, _ in heads].index(start)][1]}'")
    return 0


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        prog="wiki_tool.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="Build catalog.jsonl from all wiki pages")

    lint_p = sub.add_parser("lint", help="Run deterministic checks (exit 1 = issues found)")
    lint_p.add_argument(
        "--show-legacy", action="store_true",
        help="List all legacy pages missing frontmatter (default: count only)",
    )
    lint_p.add_argument(
        "--strict", action="store_true",
        help="Exit 1 only for hard errors (C, G-data, H); treat legacy/orphan/index as warnings",
    )

    scan_p = sub.add_parser("source-scan", help="Show raw sources not yet ingested")
    scan_p.add_argument("--all", action="store_true", help="Show all sources including ingested")

    search_p = sub.add_parser("search", help="Rank pages for one or more terms (regex, case-insensitive)")
    search_p.add_argument("terms", nargs="+", help="One term per concept; aliases as alternation, e.g. 'eks|kubernetes'")
    search_p.add_argument("--limit", type=int, default=8)

    links_p = sub.add_parser("links", help="Outgoing links with summaries + inbound links for a page")
    links_p.add_argument("page")

    sub.add_parser("sections", help="List index.md section headings")

    idx_p = sub.add_parser("index-add", help="Insert a row into an index.md section")
    idx_p.add_argument("--section", required=True)
    idx_p.add_argument("--page", required=True)
    idx_p.add_argument("--description", required=True)

    args = p.parse_args()
    if args.command == "build":
        sys.exit(cmd_build(args))
    elif args.command == "lint":
        sys.exit(cmd_lint(args))
    elif args.command == "source-scan":
        sys.exit(cmd_source_scan(args))
    else:
        sys.exit({"search": cmd_search, "links": cmd_links, "sections": cmd_sections,
                  "index-add": cmd_index_add}[args.command](args))


if __name__ == "__main__":
    main()
