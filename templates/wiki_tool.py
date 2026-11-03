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
  set-type        Set or infer frontmatter type on wiki pages
  export-okf      Export wiki pages as an Open Knowledge Format v0.2 bundle
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
  V  Stale verification — content changed since review       [quality]
  T  Page types — missing or invalid type                    [quality]
  S  Stale pages — past stale_after date                     [quality]
"""

import argparse
import datetime
import hashlib
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
    # body_start(): the same boundary the trust-field stripper uses, or fields past a `---\r\n` slip through
    lines = text.splitlines(keepends=True)
    start = body_start(lines)
    if not start:
        return None, text
    return _parse_fm_block("".join(lines[1:start - 1])), "".join(lines[start:])


TRUST_KEYS = ("generated", "verified")
ACTOR_RE = re.compile(r"^[\w.@:-]{1,64}$")


def _fm_key(line: str) -> str | None:
    """Top-level key of a frontmatter line, read the way _parse_fm_block reads it (`key :`, `'key':`)."""
    if ":" not in line or line.startswith((" ", "#")):  # not "\t": the parser reads `\tverified:` as a key
        return None
    return line.split(":", 1)[0].strip().strip("'\"")


def _split_trust(fm_lines: list[str], strip: tuple[str, ...] = TRUST_KEYS) -> tuple[list[str], list[str]]:
    """Split frontmatter lines into (other lines, `verified:` block lines); other `strip` keys are dropped.
    Keys are matched the way _parse_fm_block reads them (`verified :`, `'verified':` included)."""
    kept: list[str] = []
    verified: list[str] = []
    current = None
    for line in fm_lines:
        if current and line.startswith((" ", "\t")):
            if current == "verified":
                verified.append(line)
            continue
        key = _fm_key(line)
        current = key if key in strip else None
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


def human_actor(name: str | None) -> str:
    """Reviewer name for `verified.by`: `jijin` -> `human:jijin`; empty -> `human`."""
    name = (name or "").strip() or "human"
    return name if name == "human" or name.startswith("human:") else f"human:{name}"


def page_hash(text: str) -> str:
    """Canonical 16-hex content hash of a page, excluding trust fields (generated & verified),
    with normalized line endings (\\r\\n -> \\n) and exactly one trailing newline."""
    cleaned = strip_trust_fields(text)
    normalized = cleaned.replace("\r\n", "\n").rstrip("\n") + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def set_verified_field(text: str, by: str, at: str, sha: str) -> str:
    """Stamp or update verified: { by, at, sha } in frontmatter."""
    if not ACTOR_RE.match(by):
        raise ValueError(f"Invalid actor {by!r}: use 1-64 letters, digits or . _ @ : -")
    verified_line = f'verified: {{ by: "{by}", at: "{at}", sha: "{sha}" }}\n'
    lines = text.splitlines(keepends=True)
    start = _frontmatter_end(lines)
    if not start:
        return "---\n" + verified_line + "---\n\n" + text
    kept = _split_trust(lines[1:start - 1], strip=("verified",))[0]
    return lines[0] + "".join(kept) + verified_line + "".join(lines[start - 1:])


PAGE_TYPES = {"Note", "Runbook", "Decision", "Reference", "Session", "Todo"}

TYPE_DEFAULT_STALE_DAYS: dict[str, int | None] = {
    "Note": 90,
    "Runbook": 180,
    "Decision": None,
    "Reference": 365,
    "Session": None,
    "Todo": 90,
}

SESSION_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(-.*)?$")


_TYPE_BY_LOWER = {t.lower(): t for t in PAGE_TYPES}


def canonical_page_type(val: str | None) -> str | None:
    """Normalize page type to canonical casing (e.g. 'runbook' -> 'Runbook'); None if not a page type."""
    return _TYPE_BY_LOWER.get(val.strip().lower()) if isinstance(val, str) else None


def infer_page_type(slug: str, text: str = "") -> str:
    """Infer page type from slug pattern and content."""
    clean = _page_slug(slug)
    if SESSION_DATE_RE.match(clean):
        return "Session"
    if clean in ("todo", "todos") or clean.endswith("-todo") or clean.endswith("-todos"):
        return "Todo"
    if clean in ("runbook", "runbooks") or clean.startswith("runbook-") or clean.endswith("-runbook") or "-runbook-" in clean:
        return "Runbook"
    if clean in ("decision", "decisions") or clean.startswith("decision-") or clean.endswith("-decision") or "-decision-" in clean:
        return "Decision"
    if (
        clean in ("reference", "references")
        or clean.startswith("reference-")
        or clean.endswith("-reference")
        or clean.startswith("cheat-sheet")
        or clean.endswith("-cheatsheet")
        or clean.endswith("-spec")
    ):
        return "Reference"

    if text:
        fm, _ = parse_frontmatter(text)
        if isinstance(fm, dict):
            atoms = fm.get("atoms", [])
            if isinstance(atoms, list) and atoms:
                decisions = sum(1 for a in atoms if isinstance(a, str) and a.strip().startswith("decision:"))
                if decisions == len(atoms):
                    return "Decision"

    return "Note"


def _as_date(val) -> datetime.date | None:
    """`2026-11-02` or `2026-11-02T10:00:00Z` -> date; anything else -> None."""
    try:
        return datetime.date.fromisoformat(str(val).strip()[:10])
    except ValueError:
        return None


def effective_stale_after(entry: dict) -> tuple[datetime.date | None, str]:
    """Calculate the effective stale_after date and reason for a page entry.
    Returns (stale_date, reason) or (None, reason) if it never goes stale."""
    status = (entry.get("status") or "").strip().lower()
    if status in ("archived", "deprecated", "superseded"):
        return None, f"status is {status}"

    explicit = _as_date(entry.get("stale_after"))
    if explicit:
        return explicit, f"explicit stale_after: {explicit.isoformat()}"

    # untyped pages age like Notes (lint T asks for the type)
    pt = canonical_page_type(entry.get("type")) or "Note"
    days = TYPE_DEFAULT_STALE_DAYS[pt]
    if days is None:
        return None, f"{pt} pages do not expire"

    # the clock restarts on any edit (server-stamped generated.at) or human review (verified.at),
    # not only when the writer remembers to bump last_updated
    stamps = [entry.get("last_updated")]
    stamps += [f.get("at") for f in (entry.get("generated"), entry.get("verified")) if isinstance(f, dict)]
    dates = [d for d in map(_as_date, stamps) if d]
    if not dates:
        return None, "no base date (last_updated, generated.at or verified.at)"
    base = max(dates)
    return base + datetime.timedelta(days=days), f"{pt} default ({days} days after {base.isoformat()})"


def set_page_type(text: str, page_type: str) -> str:
    """Stamp or update type: <Type> in frontmatter."""
    canon = canonical_page_type(page_type)
    if not canon:
        raise ValueError(f"Invalid page type {page_type!r}: choose from {', '.join(sorted(PAGE_TYPES))}")
    lines = text.splitlines(keepends=True)
    start = _frontmatter_end(lines)
    type_line = f"type: {canon}\n"
    if not start:
        # a type-only frontmatter would hide the legacy header from convert-legacy for good
        raise ValueError("no frontmatter: run convert-legacy first (it sets the type)")

    fm_lines = lines[1:start - 1]
    new_fm = []
    type_found = False
    for line in fm_lines:
        if _fm_key(line) == "type":
            new_fm.append(type_line)
            type_found = True
        else:
            new_fm.append(line)

    if not type_found:
        insert_idx = 0
        for i, line in enumerate(new_fm):
            if _fm_key(line) in ("title", "summary"):
                insert_idx = i + 1
        new_fm.insert(insert_idx, type_line)

    return lines[0] + "".join(new_fm) + "".join(lines[start - 1:])


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
# shortcut: only rows with a leading pipe count; a table written without one (`a | b`) still pairs across rows
TABLE_ROW_RE = re.compile(r"(?m)(^[ ]{0,3}\|[^\n]*\n?)")


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

    # each table row is its own block (GFM), so a stray backtick in one row cannot pair with the next row's
    masked = "".join(INLINE_CODE_RE.sub(repl, part) for part in TABLE_ROW_RE.split(intermediate))
    return masked, tokens


def unmask_code(text: str, tokens: list[str]) -> str:
    """Reverse mask_code, restoring original code blocks and inline spans."""
    for i in range(len(tokens) - 1, -1, -1):
        target = f"\x00CODE{i}\x00" + ("\n" * tokens[i].count("\n"))
        text = text.replace(target, tokens[i])
    return text


def heading_id(text: str) -> str:
    """GitHub-style heading anchor: `Setup Steps (v2)` -> `setup-steps-v2`; a link target cannot hold spaces."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def rewrite_wiki_link(match: re.Match) -> str:
    """Rewrite a [[wiki-link]] match into an OKF-compliant markdown link."""
    raw = match.group(1).strip()
    if "|" in raw:
        target_part, _, display_part = raw.partition("|")
        display = display_part.strip()
    else:
        target_part = raw
        display = None

    target_part = target_part.strip()
    if "#" in target_part:
        slug_part, _, anchor = target_part.partition("#")
        anchor = anchor.strip()
    else:
        slug_part = target_part
        anchor = ""

    slug = slug_part.strip().lstrip("/")
    if slug.endswith(".md"):
        slug = slug[:-3]

    if not display:
        display = slug if slug else anchor

    clean_anchor = heading_id(anchor.lstrip("#"))
    if not slug:
        href = f"#{clean_anchor}"
    else:
        href = f"/{slug}.md"
        if clean_anchor:
            href += f"#{clean_anchor}"

    return f"[{display}]({href})"


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
            sc = int(fm.get("source_count", len(sources)))  # missing: nothing to disagree with
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
            "type": fm.get("type"),
            "status": fm.get("status"),
            "stale_after": fm.get("stale_after"),
            "summary": fm.get("summary", ""),
            "sources": sources,
            "source_count": sc,
            "provenance": provenance,
            "aliases": aliases,
            "atoms": atoms,
            "last_updated": fm.get("last_updated", ""),
            "verified": fm.get("verified"),
            "generated": fm.get("generated"),
            "wiki_links": wiki_links_in(text),
            "has_frontmatter": True,
        }

    fields = parse_legacy_fields(text)
    sources = fields.get("sources", [])
    return {
        "page": f"{WIKI.name}/{path.name}",
        "title": fields.get("title", ""),
        "type": None,
        "status": None,
        "stale_after": None,
        "summary": fields.get("summary", ""),
        "sources": sources,
        "source_count": len(sources),
        "provenance": fields.get("provenance", []),
        "aliases": [],
        "atoms": [],
        "last_updated": fields.get("last_updated", ""),
        "verified": None,
        "generated": None,
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
    issues: dict[str, list[tuple[str, str]]] = {c: [] for c in "GBCDHIJKVTS"}

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

    # V — Stale verification (content changed since human review)
    for name, e in entries.items():
        if not e["has_frontmatter"]:
            continue
        v = e.get("verified")
        if isinstance(v, dict) and v.get("sha"):
            page_text = (WIKI / name).read_text(encoding="utf-8")
            actual_hash = page_hash(page_text)
            if v["sha"] != actual_hash:
                reviewer = v.get("by", "human")
                at_date = v.get("at", "unknown")
                issues["V"].append((name, f"content changed since review by {reviewer} on {at_date} (sha {v['sha']} != {actual_hash})"))

    # T — Page types (missing or invalid)
    for name, e in entries.items():
        if not e["has_frontmatter"]:
            continue
        raw_type = e.get("type")
        if not raw_type:
            issues["T"].append((name, f"missing frontmatter 'type' (choose from {', '.join(sorted(PAGE_TYPES))})"))
        elif not canonical_page_type(raw_type):
            issues["T"].append((name, f"invalid page type {raw_type!r} (choose from {', '.join(sorted(PAGE_TYPES))})"))

    # S — Stale pages (past stale_after date)
    today = datetime.date.today()
    for name, e in entries.items():
        if not e["has_frontmatter"]:
            continue
        if e.get("stale_after") and not _as_date(e["stale_after"]):
            issues["S"].append((name, f"invalid stale_after {e['stale_after']!r} (use YYYY-MM-DD); using the type default"))
        stale_date, reason = effective_stale_after(e)
        if stale_date and today >= stale_date:
            days_ago = (today - stale_date).days
            ago_str = "today" if days_ago == 0 else (f"1 day ago" if days_ago == 1 else f"{days_ago} days ago")
            issues["S"].append((name, f"expired on {stale_date.isoformat()} ({ago_str}; {reason})"))

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
        "V": "Stale verification (content changed since human review)",
        "T": "Page types (missing or invalid type)",
        "S": "Stale pages (past stale_after date)",
    }
    # In strict mode only G-data-errors, C, and H are hard errors that block commits.
    # G-legacy (missing frontmatter), B, D, I, J, K, V, T, and S are soft warnings.
    strict = getattr(args, "strict", False)
    limit = None if getattr(args, "all", False) else max(1, getattr(args, "limit", 20))

    total = sum(len(v) for v in issues.values()) + len(legacy_pages)
    hard  = len(issues["C"]) + len(issues["G"]) + len(issues["H"])

    if total == 0:
        print("✓ No issues found.")
        return 0

    n = 1
    for check in "GBCDHIJKVTS":
        items = issues[check]
        soft_tag = "  [warning — not blocking]" if strict and check in ("B", "D", "I", "J", "K", "V", "T", "S") else ""

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


def convert_legacy(text: str, slug: str = "") -> tuple[str | None, str]:
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
    inferred_type = infer_page_type(slug, text) if slug else "Note"
    fm = ["---", f"title: {_q(lines[t][2:].strip())}", f"type: {inferred_type}", f"summary: {_q(meta['Summary'])}"]
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


def cmd_hash(args) -> int:
    path = WIKI / f"{_page_slug(args.page)}.md"
    if not path.is_file():
        print(f"No page '{path.stem}'.", file=sys.stderr)
        return 1
    print(page_hash(path.read_text(encoding="utf-8")))
    return 0


def cmd_verify(args) -> int:
    by = human_actor(args.by)
    at_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rc = 0
    for page in args.pages:
        path = WIKI / f"{_page_slug(page)}.md"
        if not path.is_file():
            print(f"No page '{path.stem}'.", file=sys.stderr)
            rc = 1
            continue
        text = path.read_text(encoding="utf-8")
        h = page_hash(text)
        try:
            spliced = set_verified_field(text, by=by, at=at_iso, sha=h)
        except ValueError as e:
            print(f"{path.name}: {e}", file=sys.stderr)
            rc = 1
            continue
        path.write_text(spliced, encoding="utf-8")
        print(f"Verified {path.name} (by: {by}, sha: {h})")
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
        new, info = convert_legacy(p.read_text(), slug=p.stem)
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


def cmd_set_type(args) -> int:
    specified_type = getattr(args, "type", None)
    if specified_type is not None:
        canon_type = canonical_page_type(specified_type)
        if not canon_type:
            print(f"Invalid page type {specified_type!r}: choose from {', '.join(sorted(PAGE_TYPES))}", file=sys.stderr)
            return 1
    else:
        canon_type = None

    if getattr(args, "all", False):
        pages = sorted(WIKI.glob("*.md"))
        targets = [p for p in pages if p.name not in SKIP]
    elif getattr(args, "pages", None):
        targets = []
        for pg in args.pages:
            p = WIKI / f"{_page_slug(pg)}.md"
            if not p.is_file():
                print(f"No page '{p.stem}'.", file=sys.stderr)
                return 1
            targets.append(p)
    else:
        print("Specify page(s) or --all.", file=sys.stderr)
        return 1

    rc = 0
    for path in targets:
        text = path.read_text(encoding="utf-8")
        fm, _ = parse_frontmatter(text)
        existing_type = canonical_page_type(fm.get("type")) if isinstance(fm, dict) else None
        target_type = canon_type or existing_type or infer_page_type(path.stem, text)
        try:
            new_text = set_page_type(text, target_type)
        except ValueError as e:
            print(f"{path.name}: {e}", file=sys.stderr)
            rc = 1
            continue
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            print(f"Set {path.name} type: {target_type}")
        else:
            print(f"Kept {path.name} type: {target_type}")
    return rc


OKF_VERSION = "0.2"
OKF_DATE_KEYS = ("last_updated", "stale_after")


def _format_iso_instant(val: str) -> str:
    """Format a date or timestamp string as full ISO 8601 with UTC offset (e.g. 2026-11-02T00:00:00Z)."""
    val = val.strip().strip("'\"")
    if not val:
        return val
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val):
        return f"{val}T00:00:00Z"
    if re.match(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$", val):
        return val.replace(" ", "T") + "Z"
    return val


def transform_frontmatter_okf(fm_lines: list[str], is_index: bool = False) -> list[str]:
    """Transform frontmatter lines: summary->description, ISO instant timestamps, okf_version on index."""
    out: list[str] = []
    has_description = any(_fm_key(l) == "description" for l in fm_lines)
    has_okf_version = any(_fm_key(l) == "okf_version" for l in fm_lines)

    if is_index and not has_okf_version:
        out.append(f'okf_version: "{OKF_VERSION}"\n')

    in_trust_block = False
    for line in fm_lines:
        k = _fm_key(line)
        if k is not None:
            in_trust_block = k in ("generated", "verified")
            if k == "summary" and not has_description:
                colon_idx = line.find(":")
                out.append("description:" + line[colon_idx + 1:])
                has_description = True
                continue
            elif k in OKF_DATE_KEYS:
                key_part, sep, val_part = line.partition(":")
                formatted = _format_iso_instant(val_part)
                out.append(f"{key_part}: {formatted}\n")
                continue
            elif k in ("generated", "verified") and "{" in line and "}" in line:
                key_part, sep, val_part = line.partition(":")
                flow = _parse_flow_mapping(val_part)
                if "at" in flow:
                    flow["at"] = _format_iso_instant(flow["at"])
                items = [f'{fk}: "{fv}"' if fk in ("at", "sha", "by") else f'{fk}: {fv}' for fk, fv in flow.items()]
                joined = ", ".join(items)
                out.append(f"{key_part}: {{ {joined} }}\n")
                continue

        if in_trust_block and line.startswith((" ", "\t")):
            m = re.match(r"^(\s*at\s*:\s*)(.*)$", line)
            if m:
                formatted = _format_iso_instant(m.group(2))
                out.append(f"{m.group(1)}{formatted}\n")
                continue

        out.append(line)

    return out


def export_okf_page(text: str, is_index: bool = False) -> str:
    """Transform a single wiki page into an OKF page."""
    lines = text.splitlines(keepends=True)
    start = body_start(lines)
    if start:
        new_fm_text = "---\n" + "".join(transform_frontmatter_okf(lines[1:start - 1], is_index=is_index)) + "---\n"
    else:
        new_fm_text = f'---\nokf_version: "{OKF_VERSION}"\n---\n\n' if is_index else ""
    masked_body, tokens = mask_code("".join(lines[start:]))
    rewritten_body = re.sub(r"\[\[([^\]]+)\]\]", rewrite_wiki_link, masked_body)
    return new_fm_text + unmask_code(rewritten_body, tokens)


def export_okf_bundle(wiki_dir: Path, output_dir: Path | None = None) -> dict[str, str]:
    """Export index.md and the wiki pages in wiki_dir as an OKF bundle {relative path: text}.
    log.md and the rotated log/ files record changes, not knowledge, so they stay out."""
    if not wiki_dir.is_dir():
        raise FileNotFoundError(f"Wiki directory not found: {wiki_dir}")
    exported = {p.name: export_okf_page(p.read_text(encoding="utf-8"), is_index=p.name == "index.md")
                for p in sorted(wiki_dir.glob("*.md")) if p.name != "log.md"}
    exported.setdefault("index.md", f'---\nokf_version: "{OKF_VERSION}"\n---\n\n# Index\n')
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, out_text in exported.items():
            (output_dir / name).write_text(out_text, encoding="utf-8")
    return exported


def cmd_export_okf(args) -> int:
    if not args.output and not args.json:
        print("export-okf: error: output directory or --json is required", file=sys.stderr)
        return 1
    exported = export_okf_bundle(WIKI, output_dir=Path(args.output) if args.output else None)
    if args.json:
        print(json.dumps({"okf_version": OKF_VERSION, "page_count": len(exported), "files": exported}, indent=2))
    if args.output:
        # stderr, so --json output on stdout stays valid JSON
        print(f"Exported {len(exported)} pages to {args.output} (OKF v{OKF_VERSION})", file=sys.stderr)
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

    hash_p = sub.add_parser("hash", help="Print canonical page hash (excluding trust fields)")
    hash_p.add_argument("page", help="Page slug or filename")

    verify_p = sub.add_parser("verify", help="Stamp verified trust field on one or more wiki pages")
    verify_p.add_argument("pages", nargs="+", help="Page slug(s) or filename(s)")
    verify_p.add_argument("--by", default="human", help="Reviewer identity (default: 'human')")

    sub.add_parser("pending", help="List raw/ files not cited by any wiki page")

    conv_p = sub.add_parser("convert-legacy", help="Convert legacy pages to frontmatter (dry run unless --apply)")
    conv_p.add_argument("--apply", action="store_true", help="Write the converted pages")

    type_p = sub.add_parser("set-type", help="Set or infer frontmatter type on wiki pages")
    type_p.add_argument("pages", nargs="*", help="Page slug(s) or filename(s)")
    type_p.add_argument("--type", "-t", default=None, help="Page type (Note, Runbook, Decision, Reference, Session, Todo). Inferred if omitted.")
    type_p.add_argument("--all", action="store_true", help="Apply to all pages in wiki/")

    export_p = sub.add_parser("export-okf", help="Export wiki pages as an Open Knowledge Format v0.2 bundle")
    export_p.add_argument("output", nargs="?", default=None, help="Output directory for the OKF bundle")
    export_p.add_argument("--json", action="store_true", help="Print exported bundle as JSON to stdout")

    args = p.parse_args()
    if args.command == "build":
        sys.exit(cmd_build(args))
    elif args.command == "lint":
        sys.exit(cmd_lint(args))
    else:
        sys.exit({"search": cmd_search, "links": cmd_links, "sections": cmd_sections,
                  "index-add": cmd_index_add, "set-atoms": cmd_set_atoms, "stamp": cmd_stamp,
                  "hash": cmd_hash, "verify": cmd_verify, "pending": cmd_pending,
                  "convert-legacy": cmd_convert_legacy, "set-type": cmd_set_type,
                  "export-okf": cmd_export_okf,
                  "rotate-log": cmd_rotate_log}[args.command](args))


if __name__ == "__main__":
    main()
