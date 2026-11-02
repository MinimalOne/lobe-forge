#!/usr/bin/env python3
"""
wiki_tool.py — deterministic CLI for a lobe (LLM wiki) repo.

Commands:
  build           Parse all wiki pages and write catalog.jsonl
  lint            Run deterministic quality checks (exits 0=ok, 1=issues found)
  search          Rank pages for one or more terms (BM25F + recency), with atoms & snippets
  links           Show a page's outgoing links (with summaries) and inbound links
  sections        List index.md section headings
  index-add       Insert one row into an index.md section without reading the whole index
  set-atoms       Write or replace the frontmatter atoms: list on a wiki page
  pending         List raw/ sources not cited by any wiki page
  convert-legacy  Move legacy bold Summary/Sources/Last updated lines into frontmatter
  rotate-log      Move log.md entries from earlier months into log/<YYYY-MM>.md

Paths default to this repo's layout (wiki/, raw/). Override with WIKI_ROOT, WIKI_DIR,
WIKI_RAW_DIR and WIKI_CATALOG to point it at another layout.

Checks run by lint:
  G  YAML frontmatter — missing, source_count mismatch,      [data error]
     or neither sources nor provenance
  B  Orphan pages — no inbound wiki-links from any page      [quality]
  C  Broken links — [[target]] page does not exist           [error]
  D  Index discrepancy — file/index out of sync              [quality]
  H  Missing source files — sources: path does not exist     [data error]
  I  Missing atoms — frontmatter has no atoms: list          [quality]
  J  Long pages — over --max-lines (default 200)             [quality]
  K  Likely duplicates — similar title/aliases/summary/atoms [quality]
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


def _split_flow_items(s: str) -> list[str]:
    """Split comma-separated key: value pairs inside { ... }, respecting quotes."""
    pairs, cur, in_quote = [], [], None
    for ch in s:
        if in_quote:
            cur.append(ch)
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
            cur.append(ch)
        elif ch == ",":
            part = "".join(cur).strip()
            if part:
                pairs.append(part)
            cur = []
        else:
            cur.append(ch)
    part = "".join(cur).strip()
    if part:
        pairs.append(part)
    return pairs


def _parse_flow_mapping(s: str) -> dict:
    inner = s.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1].strip()
    result = {}
    for item in _split_flow_items(inner):
        if ":" in item:
            k, _, v = item.partition(":")
            result[k.strip()] = _unquote(v)  # flat only: trust fields never nest
    return result


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
            elif val.startswith("{") and val.endswith("}"):
                result[key] = _parse_flow_mapping(val)
                i += 1
            elif val:
                result[key] = _unquote(val)
                i += 1
            else:
                # Collect sub-block on following lines
                i += 1
                sub_lines = []
                while i < len(lines) and (lines[i].startswith("  ") or not lines[i].strip()):
                    sub_lines.append(lines[i])
                    i += 1
                has_list_items = any(l.startswith("  - ") for l in sub_lines if l.strip())
                if has_list_items:
                    items = []
                    for l in sub_lines:
                        if l.startswith("  - "):
                            item_str = l[4:].strip()
                            if item_str.startswith("{") and item_str.endswith("}"):
                                items.append(_parse_flow_mapping(item_str))
                            else:
                                items.append(_unquote(item_str))
                    result[key] = items
                else:
                    sub_dict = {}
                    for l in sub_lines:
                        sl = l.strip()
                        if sl and not sl.startswith("#") and ":" in sl:
                            sk, _, sv = sl.partition(":")
                            sk = sk.strip()
                            sv = sv.strip()
                            if sv.startswith("{") and sv.endswith("}"):
                                sub_dict[sk] = _parse_flow_mapping(sv)
                            else:
                                sub_dict[sk] = _unquote(sv)
                    result[key] = sub_dict or []  # an empty `key:` stays a list, as before
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


TRUST_KEYS = ("generated", "verified")
ACTOR_RE = re.compile(r"^[\w.@:-]{1,64}$")


def _split_trust(fm_lines: list[str]) -> tuple[list[str], list[str]]:
    """Split frontmatter lines into (other lines, `verified:` block lines); `generated:` is dropped.
    Keys are matched the way _parse_fm_block reads them (`verified :`, `'verified':` included)."""
    kept: list[str] = []
    verified: list[str] = []
    current = None
    for line in fm_lines:
        if current and line.startswith((" ", "\t")):
            if current == "verified":
                verified.append(line)
            continue
        key = line.split(":", 1)[0].strip().strip("'\"") if ":" in line and not line.startswith((" ", "\t", "#")) else None
        current = key if key in TRUST_KEYS else None
        if current == "verified":
            verified.append(line)
        elif not current:
            kept.append(line)
    return kept, verified


def _frontmatter_end(lines: list[str]) -> int:
    """body_start(), but an opening `---` with no closing line is an error, not a page without frontmatter."""
    start = body_start(lines)
    if not start and lines and lines[0].rstrip("\r\n") == "---":
        raise ValueError("Frontmatter has no closing '---' line")
    return start


def strip_trust_fields(text: str) -> str:
    """The page with `generated:` and `verified:` removed from its frontmatter."""
    lines = text.splitlines(keepends=True)
    start = body_start(lines)
    if not start:
        return text
    return lines[0] + "".join(_split_trust(lines[1:start - 1])[0]) + "".join(lines[start - 1:])


def strip_and_splice_trust_fields(
    text: str,
    existing_disk_text: str | None = None,
    actor: str = "mcp-client",
    at_iso: str | None = None,
) -> str:
    """
    Server-owned trust fields:
    - Strip any inbound `generated:` and `verified:` blocks from `text`.
    - Carry forward the `verified:` block from `existing_disk_text` (if any).
    - Restamp `generated: { by: "<actor>", at: "<iso_utc>" }`.
    Raises ValueError for an actor that is not a plain identifier or unclosed frontmatter.
    """
    if not ACTOR_RE.match(actor):
        raise ValueError(f"Invalid actor {actor!r}: use 1-64 letters, digits or . _ @ : -")
    if at_iso is None:
        at_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    disk_verified: list[str] = []
    if existing_disk_text:
        disk_lines = existing_disk_text.splitlines(keepends=True)
        disk_start = body_start(disk_lines)
        if disk_start:
            disk_verified = _split_trust(disk_lines[1:disk_start - 1])[1]
    gen_line = f'generated: {{ by: "{actor}", at: "{at_iso}" }}\n'

    lines = text.splitlines(keepends=True)
    start = _frontmatter_end(lines)
    if not start:
        return "---\n" + "".join(disk_verified) + gen_line + "---\n\n" + text
    kept = _split_trust(lines[1:start - 1])[0]
    return lines[0] + "".join(kept + disk_verified) + gen_line + "".join(lines[start - 1:])


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


# ── Code Masking ─────────────────────────────────────────────────────────────

FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")
LIST_ITEM_RE = re.compile(r"^[ ]*(?:[*+-]|\d+[.)])[ ]+")
INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n\s*\n).)*?)(?<!`)\1(?!`)", re.DOTALL)


def body_start(lines: list[str]) -> int:
    """Index of the first line after the YAML frontmatter (0 if there is none or it is unclosed)."""
    if lines and lines[0].rstrip("\r\n") == "---":
        return next((i + 1 for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), 0)
    return 0


def code_line_indices(lines: list[str]) -> set[int]:
    """Return line indices (0-based) of fenced or 4-space indented code blocks, skipping frontmatter."""
    code_lines: set[int] = set()
    in_fence = False
    fence_char = ""
    fence_len = 0
    in_indent = False
    in_list = False  # indented lines inside a list item are continuation, not code (CommonMark)
    prev_blank = True

    for i in range(body_start(lines), len(lines)):
        raw = lines[i].rstrip("\r\n")
        if in_fence:
            code_lines.add(i)
            # Closing fence: 0-3 leading spaces, matching fence char of length >= fence_len, optional trailing whitespace
            if re.match(r"^[ ]{0,3}" + re.escape(fence_char) + r"{" + str(fence_len) + r",}[ \t]*$", raw):
                in_fence = False
                fence_char = ""
                fence_len = 0
            continue

        m = FENCE_OPEN_RE.match(raw)
        if m:
            c = m.group(1)[0]
            flen = len(m.group(1))
            info = m.group(2)
            # Backtick fence info string cannot contain backticks
            if not (c == "`" and "`" in info):
                in_fence = True
                fence_char = c
                fence_len = flen
                in_indent = False
                code_lines.add(i)
                continue

        if not raw.strip():
            prev_blank = True
            continue

        if raw.startswith(("    ", "\t")) and not in_list and (in_indent or prev_blank):
            in_indent = True
            code_lines.add(i)
            prev_blank = False
            continue
        in_indent = False
        if LIST_ITEM_RE.match(raw):
            in_list = True
        elif prev_blank and not raw.startswith((" ", "\t")):
            in_list = False  # a new unindented block ends the list; without a blank line it is lazy continuation
        prev_blank = False

    return code_lines


def mask_code(text: str) -> tuple[str, list[str]]:
    """
    Replace fenced code blocks, 4-space indented code blocks, and inline code spans
    with sentinel tokens (\\x00CODE<index>\\x00). Preserves newlines so line counts
    remain invariant. Returns (masked_text, tokens).
    """
    lines = text.splitlines(keepends=True)
    code_lines = code_line_indices(lines)
    tokens: list[str] = []
    out_parts: list[str] = []

    i = 0
    while i < len(lines):
        if i in code_lines:
            start = i
            while i < len(lines) and i in code_lines:
                i += 1
            block = "".join(lines[start:i])
            tok = f"\x00CODE{len(tokens)}\x00"
            tokens.append(block)
            out_parts.append(tok + ("\n" * block.count("\n")))
        else:
            out_parts.append(lines[i])
            i += 1

    intermediate = "".join(out_parts)

    def repl(m: re.Match) -> str:
        span = m.group(0)
        tok = f"\x00CODE{len(tokens)}\x00"
        tokens.append(span)
        return tok + ("\n" * span.count("\n"))

    masked = INLINE_CODE_RE.sub(repl, intermediate)
    return masked, tokens


def wiki_links_in(text: str) -> list[str]:
    # Strip pipe aliases ([[page|display]]) and anchors ([[page#section]]) after masking code
    masked, _ = mask_code(text)
    raw = re.findall(r"\[\[([^\]]+)\]\]", masked)
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
        atoms = fm.get("atoms", [])
        if isinstance(atoms, str):
            atoms = [atoms]
        return {
            "page": f"{WIKI.name}/{path.name}",
            "title": fm.get("title", ""),
            "summary": fm.get("summary", ""),
            "sources": sources,
            "source_count": sc,
            "provenance": provenance,
            "aliases": aliases,
            "atoms": atoms,
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
        "atoms": [],
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
    issues: dict[str, list[tuple[str, str]]] = {c: [] for c in "GBCDHIJK"}

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
        if not e.get("atoms"):
            issues["I"].append((name, "frontmatter 'atoms' is empty or missing (use set-atoms to add commands/facts/decisions)"))

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

    # J — Long pages: past this size a page dilutes search and costs more to read and update
    max_lines = getattr(args, "max_lines", 200)
    for name in entries:
        lines = (WIKI / name).read_text().splitlines()
        if len(lines) > max_lines:
            code_lines = code_line_indices(lines)
            n_sec = sum(1 for i in range(body_start(lines), len(lines)) if i not in code_lines and lines[i].startswith("## "))
            issues["J"].append((name, f"{len(lines)} lines, {n_sec} ## section(s): split into a hub page plus sub-pages"))
    issues["J"].sort(key=lambda it: -int(it[1].split()[0]))

    # K — Likely duplicates
    for a, b, score, shared in _similar_pairs(entries, getattr(args, "dup_threshold", 0.5)):
        issues["K"].append((a, f"similar to {b} ({score:.0%} shared words: {', '.join(shared)}): merge them, or link and narrow each"))

    # ── Report ────────────────────────────────────────────────────────────────
    labels = {
        "G": "Frontmatter issues",
        "B": "Orphan pages (no inbound links)",
        "C": "Broken wiki-links",
        "D": "Index discrepancies",
        "H": "Missing source files",
        "I": "Missing atoms (commands, error fixes, facts, decisions)",
        "J": f"Long pages (over {max_lines} lines)",
        "K": "Likely duplicates (similar title, aliases, summary and atoms)",
    }
    # In strict mode only G-data-errors, C, and H are hard errors that block commits.
    # G-legacy (missing frontmatter), B, D, I, J, and K are soft warnings.
    strict = getattr(args, "strict", False)
    limit = None if getattr(args, "all", False) else max(1, getattr(args, "limit", 20))

    total = sum(len(v) for v in issues.values()) + len(legacy_pages)
    hard  = len(issues["C"]) + len(issues["G"]) + len(issues["H"])

    if total == 0:
        print("✓ No issues found.")
        return 0

    n = 1
    for check in "GBCDHIJK":
        items = issues[check]
        soft_tag = "  [warning — not blocking]" if strict and check in ("B", "D", "I", "J", "K") else ""

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
            n = _print_issues(items, n, limit)
        elif items:
            print(f"\n{check}. {labels[check]} ({len(items)}){soft_tag}")
            if check == "H" and limit is not None and len(items) > limit:
                _summarize_missing_sources(items)
            n = _print_issues(items, n, limit)

    if strict:
        if hard == 0:
            print(f"\n✓ Strict mode: 0 hard errors (commit allowed). {total} soft warning(s) above.")
            return 0
        else:
            print(f"\n✗ Strict mode: {hard} hard error(s) — commit blocked.")
            return 1

    print(f"\nTotal: {total} issue(s)")
    return 1


def _similar_pairs(entries: dict[str, dict], threshold: float) -> list[tuple[str, str, float, list[str]]]:
    """Unlinked page pairs whose slug, title, aliases, summary and atoms share >= threshold of their words (Jaccard)."""
    names = sorted(n for n, e in entries.items() if e["has_frontmatter"])
    words: dict[str, set[str]] = {}
    for n in names:
        e = entries[n]
        text = " ".join([n[:-3].replace("-", " "), e["title"], e["summary"], *e["aliases"], *e["atoms"]]).lower()
        ws = set()
        for t in re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]", text):
            if t in _STOPWORDS or t in _ATOM_KINDS:
                continue
            ws.add(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t)  # backups == backup
        words[n] = ws
    df: dict[str, int] = {}
    for ws in words.values():
        for t in ws:
            df[t] = df.get(t, 0) + 1
    # words on >10% of pages say nothing about duplication, and are most of the pair work
    max_df = max(2, len(names) // 10)
    words = {n: {t for t in ws if df[t] <= max_df} for n, ws in words.items()}
    postings: dict[str, list[str]] = {}
    for n, ws in words.items():
        for t in ws:
            if df[t] >= 2:
                postings.setdefault(t, []).append(n)
    shared: dict[tuple[str, str], int] = {}
    for docs in postings.values():
        for i, a in enumerate(docs):
            for b in docs[i + 1:]:
                shared[(a, b)] = shared.get((a, b), 0) + 1
    out = []
    for (a, b), c in shared.items():
        if c < 3:  # one or two shared rare words (a port, a year) are a coincidence, not a duplicate
            continue
        if b[:-3] in entries[a]["wiki_links"] or a[:-3] in entries[b]["wiki_links"]:
            continue  # linked pairs were already judged related: hub and sub-page, merged pointer, link and narrow
        score = c / (len(words[a]) + len(words[b]) - c)
        if score >= threshold:
            out.append((a, b, score, sorted(words[a] & words[b], key=lambda t: (df[t], t))[:5]))
    return sorted(out, key=lambda x: (-x[2], x[0], x[1]))


def _print_issues(items: list[tuple[str, str]], n: int, limit: int | None) -> int:
    """Print up to `limit` numbered issues (all when None); return the next number."""
    shown = items if limit is None else items[:limit]
    for page, msg in shown:
        print(f"  {n}. {page} — {msg}")
        n += 1
    if len(shown) < len(items):
        print(f"  … and {len(items) - len(shown)} more (lint --all lists every issue)")
    return n


def _summarize_missing_sources(items: list[tuple[str, str]]) -> None:
    """One line per folder, so a systematic gap (e.g. a gitignored raw/) shows as its cause, not 800 lines."""
    srcs = {m.group(1) for _, msg in items if (m := re.match(r"source '(.+)' not found", msg))}
    by_dir: dict[str, int] = {}
    for src in srcs:
        parts = Path(src).parts
        d = str(Path(*parts[:2])) if len(parts) > 2 else str(Path(src).parent)
        by_dir[d] = by_dir.get(d, 0) + 1
    print(f"  {len(srcs)} distinct file(s) cited by {len({pg for pg, _ in items})} page(s), by folder:")
    for d, c in sorted(by_dir.items(), key=lambda kv: (-kv[1], kv[0]))[:10]:
        print(f"    {d}/ — {c} file(s){'' if (REPO / d).is_dir() else '  (folder missing)'}")
    if len(by_dir) > 10:
        print(f"    … and {len(by_dir) - 10} more folder(s)")
    if not RAW.is_dir() or not any(not f.name.startswith(".") for f in RAW.iterdir()):  # .gitkeep alone is empty
        print(f"  {RAW.name}/ is missing or empty: restore the sources (is {RAW.name}/ gitignored?)")


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


def _locate_match(full_text: str, body: str, rx: re.Pattern) -> str:
    """Return a snippet prefixed with [L<start>-<end> ## Section] (1-based file line numbers)."""
    lines = full_text.splitlines()
    n_lines = len(lines)
    start = body_start(lines)
    # lines inside code fences and indented blocks: "# comment" / "## comment" there are code, not headings
    code = code_line_indices(lines)
    h2_indices = [i for i in range(start, n_lines) if i not in code and lines[i].startswith("## ")]
    h1 = next((i for i in range(start, n_lines) if i not in code and lines[i].startswith("# ")), None)
    for idx in range(start, n_lines):
        if idx == h1:  # the title; cmd_search strips it from body too
            continue
        if rx.search(lines[idx]):
            sec_heading = ""
            sec_start, sec_end = idx + 1, min(n_lines, idx + 10)
            for k, h_idx in enumerate(h2_indices):
                if h_idx <= idx:
                    sec_heading = lines[h_idx].strip()
                    sec_start = h_idx + 1
                    sec_end = h2_indices[k + 1] if k + 1 < len(h2_indices) else n_lines
                else:
                    break
            # neighbouring headings are noise, but keep the matched line even when it is a heading
            ctx = [lines[j].strip() for j in range(max(start, idx - 1), min(n_lines, idx + 2))
                   if lines[j].strip() and (j == idx or j in code or not re.match(r"^#+ ", lines[j]))]
            win = " ".join(ctx) if ctx else lines[idx].strip()
            m = rx.search(win)
            raw_snip = win[max(0, m.start() - 70): m.end() + 70] if m else win
            tag = f"[L{sec_start}-{sec_end} {sec_heading}]" if sec_heading else f"[L{idx + 1}-{sec_end}]"
            return f"{tag} {_one_line(raw_snip, 150)}"
    m = rx.search(body)
    return _one_line(body[max(0, m.start() - 70): m.end() + 70], 170) if m else ""


_ATOM_KINDS = {"cmd", "fix", "fact", "decision"}

_STOPWORDS = {"the", "and", "for", "how", "what", "why", "when", "where", "which", "who", "with",
              "does", "did", "can", "are", "was", "not", "this", "that", "from", "into", "about", "you", "your",
              "to", "in", "on", "at", "by", "of", "or", "is", "it", "as", "be", "do", "no", "so", "up", "if",
              "my", "me", "we", "an", "am"}


def _expand_terms(raw_terms: list[str]) -> list[tuple[re.Pattern, int]]:
    """Compile each term as (pattern, concept index); multi-word plain phrases also yield whole-word
    token patterns (stopwords and 1-char tokens dropped) under the same concept index, so a concept
    matched only through its words still counts once toward the multi-concept boost."""
    out: list[tuple[re.Pattern, int]] = []
    seen: set[str] = set()
    for ci, t in enumerate(raw_terms):
        t = t.strip()
        if not t:
            continue
        if t.lower() not in seen:
            seen.add(t.lower())
            out.append((_compile(t), ci))
        if " " in t and "|" not in t:
            for tok in re.findall(r"[\w-]+", t):
                if len(tok) >= 2 and tok.lower() not in _STOPWORDS and tok.lower() not in seen:
                    seen.add(tok.lower())
                    out.append((re.compile(rf"\b{re.escape(tok)}\b", re.IGNORECASE), ci))
    return out


def _rank_docs(docs: list[tuple], raw_terms: list[str], ref_date: datetime.date, no_decay: bool) -> list[tuple]:
    terms = _expand_terms(raw_terms)
    if not docs or not terms:
        return []
    n_docs = len(docs)
    avgdl = sum(dl for _, _, _, _, dl, _ in docs) / n_docs
    k1, b = 1.5, 0.75

    idfs = []
    for rx, _ in terms:
        df = sum(
            1 for slug, e, _, body, _, _ in docs
            if rx.search(slug)
            or rx.search(e["title"])
            or any(rx.search(a) for a in e["aliases"])
            or any(rx.search(a) for a in e["atoms"])
            or rx.search(e["summary"])
            or rx.search(body)
        )
        idfs.append(math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5)) if df else 0.0)

    hits = []
    for slug, e, full_text, body, dl, total_lines in docs:
        raw_score, snippet = 0.0, ""
        hit_concepts: set[int] = set()
        matched_atoms: list[str] = []
        for (rx, ci), idf in zip(terms, idfs):
            if idf <= 0.0:
                continue
            field_s = 0.0
            if rx.search(slug):
                field_s += 10.0
            if rx.search(e["title"]):
                field_s += 8.0
            if any(rx.search(a) for a in e["aliases"]):
                field_s += 8.0
            atom_hits = [a for a in e["atoms"] if rx.search(a)]
            if atom_hits:
                field_s += 6.0
                for a in atom_hits:
                    if a not in matched_atoms:
                        matched_atoms.append(a)
            if rx.search(e["summary"]):
                field_s += 4.0
            found = rx.findall(body)
            tf = len(found)
            body_bm25 = (tf * (k1 + 1.0)) / (tf + k1 * (1.0 - b + b * (dl / avgdl))) if tf else 0.0
            term_s = field_s + 2.0 * body_bm25
            if term_s > 0.0:
                hit_concepts.add(ci)
                raw_score += idf * term_s
                if not snippet and found:
                    snippet = _locate_match(full_text, body, rx)
        if raw_score > 0.0:
            co_boost = raw_score * (1.0 + 0.5 * (len(hit_concepts) - 1))
            recency = 1.0 if no_decay else _recency_weight(e["last_updated"], ref_date)
            score = round(co_boost * recency, 2)
            hits.append((score, slug, e, total_lines, matched_atoms, snippet))
    hits.sort(key=lambda h: (-h[0], h[1]))
    return hits


def _print_hits(hits: list[tuple], limit: int) -> None:
    if not hits:
        print("No matching pages.")
        return
    for score, slug, e, total_lines, matched_atoms, snippet in hits[:limit]:
        print(f"[[{slug}]] score={score:.1f} updated={e['last_updated'] or '?'} lines={total_lines} — {e['title']}")
        if e["summary"]:
            print(f"    summary: {_one_line(e['summary'], 200)}")
        for a in matched_atoms[:3]:
            print(f"    atom:    {_one_line(a, 200)}")
        if snippet:
            print(f"    match:   {snippet}")
    print(f"\n{min(len(hits), limit)} of {len(hits)} matching page(s) shown")


def cmd_search(args) -> int:
    docs = []
    for p in _content_pages():
        text = p.read_text()
        e = parse_page(p, text)
        _, body = parse_frontmatter(text)
        body = re.sub(r"\A\s*# .*\n", "", body)  # H1 repeats the title; keep snippets informative
        docs.append((p.stem, e, text, body, max(1, len(body.split())), len(text.splitlines())))

    ref_date = datetime.date.today()  # not max(last_updated): one future-dated typo would age every page
    no_decay = getattr(args, "no_decay", False)
    if getattr(args, "batch", False):
        for idx, q in enumerate(args.terms):
            if idx:
                print()
            print(f"=== query: {q} ===")
            _print_hits(_rank_docs(docs, [q], ref_date, no_decay), args.limit)
        return 0

    hits = _rank_docs(docs, args.terms, ref_date, no_decay)
    if not hits:
        print("No matching pages.")
        return 1
    _print_hits(hits, args.limit)
    return 0


def _page_slug(arg: str) -> str:
    """`wiki/foo.md`, `foo.md` or `foo` -> `foo`."""
    return arg.removesuffix(".md").removeprefix(f"{WIKI.name}/")


def cmd_links(args) -> int:
    entries = {p.stem: parse_page(p) for p in _content_pages()}
    slug = _page_slug(args.page)
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


# ── rotate-log ────────────────────────────────────────────────────────────────

LOG_ARCHIVE = WIKI / "log"


def cmd_rotate_log(args) -> int:
    """Move log.md entries from months before this one into log/<YYYY-MM>.md, keeping their order."""
    LOG_ARCHIVE.mkdir(exist_ok=True)  # always: skills `git add` it, and a missing path would fail that commit
    log = WIKI / "log.md"
    if not log.exists():
        return 0
    text = log.read_text()
    heads = [m.start() for m in re.finditer(r"(?m)^## ", text)]
    if not heads:
        return 0
    month = getattr(args, "month", None) or datetime.date.today().strftime("%Y-%m")
    header, keep, moved = text[: heads[0]], [], {}
    for start, end in zip(heads, heads[1:] + [len(text)]):
        entry = text[start:end]
        m = re.match(r"## .*?(\d{4}-\d{2})-\d{2}", entry)
        if m and m.group(1) < month:
            moved.setdefault(m.group(1), []).append(entry)
        else:
            keep.append(entry)  # this month, or no date: stays
    if not moved:
        return 0
    title = header.splitlines()[0].lstrip("# ").strip() if header.strip() else "Log"
    for ym, entries in sorted(moved.items()):
        f = LOG_ARCHIVE / f"{ym}.md"
        old = f.read_text() if f.exists() else f"# {title} — {ym}\n\n"
        f.write_text(old.rstrip("\n") + "\n\n" + "".join(entries).strip("\n") + "\n")
        print(f"{WIKI.name}/log/{ym}.md: {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
    pointer = f"Older entries: `{WIKI.name}/log/<YYYY-MM>.md`, one file per month.\n"
    if pointer not in header:
        header = header.rstrip("\n") + "\n\n" + pointer
    body = "".join(keep).strip("\n")
    log.write_text(header.rstrip("\n") + ("\n\n" + body + "\n" if body else "\n"))
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


def cmd_set_atoms(args) -> int:
    slug = _page_slug(args.page)
    path = WIKI / f"{slug}.md"
    if not path.is_file():
        print(f"No page '{slug}'.", file=sys.stderr)
        return 1
    atoms = [" ".join(a.split()) for a in args.atoms if a.strip()]
    if not atoms:
        print("No non-blank atoms given.", file=sys.stderr)
        return 1
    text = path.read_text()
    if not text.startswith("---\n"):
        print(f"{path.name} has no frontmatter (run convert-legacy --apply first).", file=sys.stderr)
        return 1
    try:
        end = text.index("\n---\n", 4)
    except ValueError:
        print(f"{path.name} has malformed frontmatter.", file=sys.stderr)
        return 1

    fm_lines = text[4:end].splitlines()
    body = text[end + 5:]
    atoms_block = ["atoms:"] + [f"  - {_q(a)}" for a in atoms]

    # Remove any existing atoms: block in frontmatter
    cleaned: list[str] = []
    i = 0
    inserted = False
    while i < len(fm_lines):
        if re.match(r"^atoms\s*:", fm_lines[i]):
            i += 1
            while i < len(fm_lines) and fm_lines[i].startswith("  - "):
                i += 1
            if not inserted:  # atoms: after sources:, or a duplicate atoms: key, must not add a second block
                cleaned.extend(atoms_block)
            inserted = True
            continue
        if not inserted and re.match(r"^(sources|source_count|provenance|last_updated)\s*:", fm_lines[i]):
            cleaned.extend(atoms_block)
            inserted = True
        cleaned.append(fm_lines[i])
        i += 1
    if not inserted:
        cleaned.extend(atoms_block)

    path.write_text("---\n" + "\n".join(cleaned) + "\n---\n" + body)
    print(f"Set {len(atoms_block) - 1} atom(s) on {path.name}")
    return 0


def cmd_stamp(args) -> int:
    rc = 0
    for page in args.pages:
        path = WIKI / f"{_page_slug(page)}.md"
        if not path.is_file():
            print(f"No page '{path.stem}'.", file=sys.stderr)
            rc = 1
            continue
        text = path.read_text(encoding="utf-8")
        try:
            spliced = strip_and_splice_trust_fields(text, existing_disk_text=text, actor=args.by)
        except ValueError as e:
            print(f"{path.name}: {e}", file=sys.stderr)
            rc = 1
            continue
        path.write_text(spliced, encoding="utf-8")
        print(f"Stamped {path.name} (by: {args.by})")
    return rc


def cmd_pending(_args) -> int:
    if not RAW.is_dir():
        print("No pending raw sources.")
        return 0
    cited = {src for p in _content_pages() for src in parse_page(p)["sources"]}

    uningested: list[str] = []
    for f in sorted(RAW.rglob("*")):
        if not f.is_file() or any(part.startswith(".") for part in f.relative_to(RAW).parts):
            continue
        rel = f.relative_to(REPO).as_posix()
        if rel not in cited:
            uningested.append(rel)

    if not uningested:
        print("No pending raw sources.")
        return 0
    print(f"Un-ingested raw sources ({len(uningested)}):")
    for r in uningested:
        print(f"  {r}")
    return 0


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
    lint_p.add_argument("--limit", type=int, default=20, help="Issues listed per check (default 20)")
    lint_p.add_argument("--all", action="store_true", help="List every issue (no per-check limit)")
    lint_p.add_argument("--max-lines", type=int, default=200, help="J: flag pages longer than this (default 200)")
    lint_p.add_argument("--dup-threshold", type=float, default=0.5,
                        help="K: flag page pairs sharing at least this share of words, 0-1 (default 0.5)")

    rot_p = sub.add_parser("rotate-log", help="Move log.md entries from earlier months into log/<YYYY-MM>.md")
    rot_p.add_argument("--month", help="Treat this YYYY-MM as the current month (default: today)")

    search_p = sub.add_parser("search", help="Rank pages for one or more terms (BM25F + recency, regex, case-insensitive)")
    search_p.add_argument("terms", nargs="+", help="One term per concept; aliases as alternation, e.g. 'eks|kubernetes'")
    search_p.add_argument("--limit", type=int, default=8)
    search_p.add_argument("--no-decay", action="store_true", help="Disable recency weighting on last_updated")
    search_p.add_argument("--batch", action="store_true", help="Run each term as an independent candidate query in one call")

    links_p = sub.add_parser("links", help="Outgoing links with summaries + inbound links for a page")
    links_p.add_argument("page")

    sub.add_parser("sections", help="List index.md section headings")

    idx_p = sub.add_parser("index-add", help="Insert a row into an index.md section")
    idx_p.add_argument("--section", required=True)
    idx_p.add_argument("--page", required=True)
    idx_p.add_argument("--description", required=True)

    atoms_p = sub.add_parser("set-atoms", help="Write or replace the frontmatter atoms: list on a wiki page")
    atoms_p.add_argument("page", help="Page slug or filename")
    atoms_p.add_argument("atoms", nargs="+", help="Concise atoms: 'cmd: ...', 'fix: ...', 'decision: ...', 'fact: ...'")

    stamp_p = sub.add_parser("stamp", help="Restamp generated trust field on one or more wiki pages")
    stamp_p.add_argument("pages", nargs="+", help="Page slug(s) or filename(s)")
    stamp_p.add_argument("--by", default="local", help="Actor identity (default: 'local')")

    sub.add_parser("pending", help="List raw/ files not cited by any wiki page")

    conv_p = sub.add_parser("convert-legacy", help="Convert legacy pages to frontmatter (dry run unless --apply)")
    conv_p.add_argument("--apply", action="store_true", help="Write the converted pages")

    args = p.parse_args()
    if args.command == "build":
        sys.exit(cmd_build(args))
    elif args.command == "lint":
        sys.exit(cmd_lint(args))
    else:
        sys.exit({"search": cmd_search, "links": cmd_links, "sections": cmd_sections,
                  "index-add": cmd_index_add, "set-atoms": cmd_set_atoms, "stamp": cmd_stamp, "pending": cmd_pending,
                  "convert-legacy": cmd_convert_legacy, "rotate-log": cmd_rotate_log}[args.command](args))


if __name__ == "__main__":
    main()
