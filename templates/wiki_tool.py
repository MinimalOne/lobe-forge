#!/usr/bin/env python3
"""
wiki_tool.py — deterministic CLI for a lobe (LLM wiki) repo.

Commands:
  build        Parse all wiki pages and write catalog.jsonl
  lint         Run deterministic quality checks (exits 0=ok, 1=issues found)
  search       Rank pages for one or more terms (title/alias/summary/body), with snippets
  links        Show a page's outgoing links (with summaries) and inbound links
  sections     List index.md section headings
  index-add    Insert one row into an index.md section without reading the whole index
  convert-legacy  Move legacy bold Summary/Sources/Last updated lines into frontmatter

Paths default to this repo's layout (wiki/, raw/). Override with WIKI_ROOT, WIKI_DIR,
WIKI_RAW_DIR and WIKI_CATALOG to point it at another layout.

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
import math
import os
import re
import sys
from pathlib import Path

REPO    = Path(os.environ.get("WIKI_ROOT") or Path(__file__).parent)
WIKI    = REPO / os.environ.get("WIKI_DIR", "wiki")
RAW     = REPO / os.environ.get("WIKI_RAW_DIR", "raw")
CATALOG = Path(os.environ.get("WIKI_CATALOG") or REPO / "catalog.jsonl")
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


def _split_top(s: str) -> list[str]:
    """Split on ';' outside () and [] — legacy Sources lines use ';' between kinds of evidence."""
    out, depth, cur = [], 0, ""
    for ch in s:
        depth += (ch in "([") - (ch in ")]")
        if ch == ";" and depth <= 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return [p for p in out + [cur.strip()] if p]


def legacy_sources(line: str) -> tuple[list[str], list[str]]:
    """(sources, provenance) from a legacy Sources line: files that exist under the raw dir are
    sources; every ';' part except a bare source path is provenance, text unchanged."""
    rel = RAW.relative_to(REPO).as_posix()
    sources: list[str] = []
    for p in re.findall(re.escape(rel) + r"/[\w./-]+\.\w+", line):
        if (REPO / p).is_file() and p not in sources:
            sources.append(p)
    provenance = [p for p in _split_top(line) if p.strip("`") not in sources]
    return sources, provenance


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
        out["sources"], out["provenance"] = legacy_sources(m.group(1).strip())
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
        "provenance": fields.get("provenance", []),
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
                print("    (convert: convert-legacy to preview, convert-legacy --apply to write)")
                print("    (H is not checked on legacy pages: convert them first)")
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


def _parse_date(date_str: str) -> datetime.date | None:
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(str(date_str).strip()[:10])
    except ValueError:
        return None


def _recency_weight(date_str: str, ref_date: datetime.date) -> float:
    """Time-decay multiplier in [0.85, 1.0]: newer pages win ties/near-ties, never real relevance gaps."""
    d = _parse_date(date_str)
    if d is None:
        return 1.0  # undated is unknown, not stale
    age_days = max(0, (ref_date - d).days)
    return max(0.85, 1.0 / (1.0 + 0.0005 * age_days))


def cmd_search(args) -> int:
    terms = [_compile(t) for t in args.terms]
    docs = []
    dates = [datetime.date.today()]
    for p in _content_pages():
        text = p.read_text()
        e = parse_page(p, text)
        _, body = parse_frontmatter(text)
        body = re.sub(r"\A\s*# .*\n", "", body)  # H1 repeats the title; keep snippets informative
        d = _parse_date(e["last_updated"])
        if d is not None:
            dates.append(d)
        docs.append((p.stem, e, body, max(1, len(body.split()))))

    if not docs:
        print("No matching pages.")
        return 1

    n_docs = len(docs)
    avgdl = sum(dl for _, _, _, dl in docs) / n_docs
    ref_date = max(dates)
    k1, b = 1.5, 0.75

    # Inverse Document Frequency (BM25 Robertson-Spärck Jones IDF) per query concept
    idfs = []
    for rx in terms:
        df = sum(
            1 for slug, e, body, _ in docs
            if rx.search(slug)
            or rx.search(e["title"])
            or any(rx.search(a) for a in e["aliases"])
            or rx.search(e["summary"])
            or rx.search(body)
        )
        idfs.append(math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) if df else 0.0)

    no_decay = getattr(args, "no_decay", False)
    hits = []
    for slug, e, body, dl in docs:
        raw_score, matched, snippet = 0.0, 0, ""
        for rx, idf in zip(terms, idfs):
            if idf <= 0.0:
                continue
            field_s = 0.0
            if rx.search(slug):
                field_s += 10.0
            if rx.search(e["title"]):
                field_s += 8.0
            if any(rx.search(a) for a in e["aliases"]):
                field_s += 8.0
            if rx.search(e["summary"]):
                field_s += 4.0
            found = rx.findall(body)
            tf = len(found)
            body_bm25 = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (dl / avgdl))) if tf else 0.0
            term_s = field_s + 2.0 * body_bm25
            if term_s > 0.0:
                matched += 1
                raw_score += idf * term_s
                if not snippet and found:
                    m = rx.search(body)
                    snippet = _one_line(body[max(0, m.start() - 70): m.end() + 70], 170)
        if raw_score > 0.0:
            # Pages matching more distinct query concepts outrank pages repeating a single concept
            co_boost = raw_score * (1.0 + 0.5 * (matched - 1))
            recency = 1.0 if no_decay else _recency_weight(e["last_updated"], ref_date)
            score = round(co_boost * recency, 2)
            hits.append((score, slug, e, snippet))

    if not hits:
        print("No matching pages.")
        return 1
    hits.sort(key=lambda h: (-h[0], h[1]))
    for score, slug, e, snippet in hits[: args.limit]:
        print(f"[[{slug}]] score={score:.1f} updated={e['last_updated'] or '?'} — {e['title']}")
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


# ── convert-legacy ────────────────────────────────────────────────────────────

LEGACY_META = re.compile(r"^\*\*(Summary|Sources?|Last updated)\*\*:\s*(.*)$")


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def convert_legacy(text: str) -> tuple[str | None, str]:
    """Return (new_text, info) or (None, reason). Only the title/metadata header changes:
    the bold lines and the '---' rule after them become frontmatter; the body is kept as is."""
    if text.startswith("---\n"):
        return None, "already has frontmatter"
    lines = text.split("\n")
    t = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
    if t is None:
        return None, "no '# ' title"
    meta, end = {}, t + 1
    while end < len(lines):
        m = LEGACY_META.match(lines[end])
        if m:
            meta["Sources" if m.group(1).startswith("Source") else m.group(1)] = m.group(2).strip()
        elif lines[end].strip() == "---":
            end += 1
            break
        elif lines[end].strip():
            break
        end += 1
    missing = sorted({"Summary", "Sources", "Last updated"} - meta.keys())
    if missing:
        return None, f"header lacks {', '.join(missing)}"
    sources, provenance = legacy_sources(meta["Sources"])
    fm = ["---", f"title: {_q(lines[t][2:].strip())}", f"summary: {_q(meta['Summary'])}"]
    fm += (["sources:"] + [f"  - {s}" for s in sources]) if sources else ["sources: []"]
    fm += [f"source_count: {len(sources)}"]
    fm += (["provenance:"] + [f"  - {_q(p)}" for p in provenance]) if provenance else []
    fm += [f"last_updated: {meta['Last updated'].split()[0]}", "---", ""]
    body = lines[end:]
    while body and not body[0].strip():
        body.pop(0)
    return "\n".join(fm + [lines[t], ""] + body), f"{len(sources)} source(s), {len(provenance)} provenance"


def cmd_convert_legacy(args) -> int:
    done = skipped = 0
    for p in sorted(WIKI.glob("*.md")):
        if p.name in SKIP:
            continue
        new, info = convert_legacy(p.read_text())
        if new is None:
            if info != "already has frontmatter":
                print(f"  skip {p.name}: {info}")
                skipped += 1
            continue
        print(f"  {'wrote' if args.apply else 'would convert'} {p.name}: {info}")
        if args.apply:
            p.write_text(new)
        done += 1
    verb = "Converted" if args.apply else "Dry run — would convert"
    print(f"\n{verb} {done} page(s); {skipped} skipped (fix by hand).")
    if done and not args.apply:
        print("Rerun with --apply to write.")
    return 1 if skipped else 0


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

    search_p = sub.add_parser("search", help="Rank pages for one or more terms (BM25F + recency, regex, case-insensitive)")
    search_p.add_argument("terms", nargs="+", help="One term per concept; aliases as alternation, e.g. 'eks|kubernetes'")
    search_p.add_argument("--limit", type=int, default=8)
    search_p.add_argument("--no-decay", action="store_true", help="Disable recency weighting on last_updated")

    links_p = sub.add_parser("links", help="Outgoing links with summaries + inbound links for a page")
    links_p.add_argument("page")

    sub.add_parser("sections", help="List index.md section headings")

    idx_p = sub.add_parser("index-add", help="Insert a row into an index.md section")
    idx_p.add_argument("--section", required=True)
    idx_p.add_argument("--page", required=True)
    idx_p.add_argument("--description", required=True)

    conv_p = sub.add_parser("convert-legacy", help="Convert legacy pages to frontmatter (dry run unless --apply)")
    conv_p.add_argument("--apply", action="store_true", help="Write the converted pages")

    args = p.parse_args()
    if args.command == "build":
        sys.exit(cmd_build(args))
    elif args.command == "lint":
        sys.exit(cmd_lint(args))
    else:
        sys.exit({"search": cmd_search, "links": cmd_links, "sections": cmd_sections,
                  "index-add": cmd_index_add, "convert-legacy": cmd_convert_legacy}[args.command](args))


if __name__ == "__main__":
    main()
