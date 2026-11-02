"""Tests for wiki_tool code masking, fence parsing, and wiki_links_in."""
import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

# Add templates to sys.path
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
if str(TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATES_DIR))

import wiki_tool

from wiki_tool import (
    PAGE_TYPES,
    _format_iso_instant,
    body_start,
    canonical_page_type,
    cmd_export_okf,
    cmd_hash,
    cmd_lint,
    cmd_set_type,
    cmd_stamp,
    cmd_verify,
    code_line_indices,
    convert_legacy,
    effective_stale_after,
    export_okf_bundle,
    export_okf_page,
    human_actor,
    infer_page_type,
    mask_code,
    page_hash,
    parse_frontmatter,
    rewrite_wiki_link,
    set_page_type,
    set_verified_field,
    strip_and_splice_trust_fields,
    strip_trust_fields,
    transform_frontmatter_okf,
    unmask_code,
    wiki_links_in,
)


def test_code_line_indices_fences():
    doc = [
        "# Title",
        "## Section 1",
        "```python",
        "## Fake In Backtick Fence",
        "[[link-in-fence]]",
        "```",
        "~~~~",
        "```python",
        "nested backtick fence inside tilde",
        "```",
        "## Fake In Tilde Fence",
        "~~~~",
        "````markdown",
        "```python",
        "nested 3-backtick inside 4-backtick fence",
        "```",
        "````",
        "## Section 2",
    ]
    code = code_line_indices(doc)
    # Check lines 2-5 (backtick fence)
    for i in range(2, 6):
        assert i in code, f"Line {i} should be in code"
    # Check lines 6-11 (tilde fence)
    for i in range(6, 12):
        assert i in code, f"Line {i} should be in code"
    # Check lines 12-16 (4-backtick fence)
    for i in range(12, 17):
        assert i in code, f"Line {i} should be in code"
    # Section headings outside code
    assert 1 not in code
    assert 17 not in code


def test_code_line_indices_indented_blocks_and_lists():
    doc = [
        "Paragraph before indent.",
        "",
        "    indented code line 1",
        "    ## Fake Header In Indent",
        "    [[link-in-indent]]",
        "",
        "Paragraph after indent.",
        "",
        "- List item 1",
        "    - Nested list item (not code)",
        "    * Another nested list item",
        "    1. Numbered nested list item",
        "",
        "    list continuation paragraph, not code",
        "    more continuation",
        "",
        "Paragraph ends the list.",
        "",
        "    indented code block after list",
        "    more code",
    ]
    code = code_line_indices(doc)
    # Indented code block at lines 2-4
    assert 2 in code and 3 in code and 4 in code
    # Paragraphs, list items and list continuation should NOT be code
    for i in (0, 6, 8, 9, 10, 11, 13, 14, 16):
        assert i not in code, f"Line {i} should not be code"
    # A blank line plus an unindented paragraph ends the list, so this is code again
    assert 18 in code and 19 in code


def test_mask_code_line_count():
    sample = """---
title: Test Document
summary: Link in summary [[frontmatter-link]]
---

# Main Title

Here is `inline [[link1]]` and ``double ` [[link2]]`` in text.

```python
# [[link3-in-fence]]
def hello():
    return "world"
```

~~~~markdown
````
nested fences
````
~~~~

    # [[link4-in-indent]]
    x = 42

- List Item
    - Nested list with [[real-nested-link]]

Regular paragraph with [[body-link]].
"""
    masked, tokens = mask_code(sample)
    # Line count must be preserved exactly
    assert masked.count("\n") == sample.count("\n"), "Line count changed during masking!"
    assert "[[link1]]" not in masked and "[[link3-in-fence]]" not in masked
    assert "[[frontmatter-link]]" in masked and "[[real-nested-link]]" in masked
    assert any("[[link3-in-fence]]" in t for t in tokens)


def test_body_start():
    assert body_start(["---", "a: 1", "---", "body"]) == 3
    assert body_start(["---", "unclosed"]) == 0
    assert body_start(["no frontmatter"]) == 0
    assert body_start([]) == 0


def test_wiki_links_in_list_continuation():
    cases = {
        "- item\n\n    continued with [[cont-link]]\n": ["cont-link"],
        "1. Step\n\n    Run this, see [[ordered-link]]\n": ["ordered-link"],
        "- step\n\n    ```bash\n    echo hi\n    ```\n\n    after fence [[after-link]]\n": ["after-link"],
        "- item\nlazy line\n\n    still in item [[lazy-link]]\n": ["lazy-link"],
        "- item\n\nNew paragraph.\n\n    [[real-code]]\n": [],
    }
    for doc, want in cases.items():
        assert wiki_links_in(doc) == want, f"{doc!r}: {wiki_links_in(doc)}"


def test_wiki_links_in_ignores_code():
    doc = """---
summary: Summary mentions [[summary-link]].
---

# Example

Body has [[valid-link-1|Alias]] and [[valid-link-2#anchor]].

Here is inline code: `[[code-inline]]` and ```[[code-inline-3backticks]]```.

Fenced code block:
```bash
curl -s http://example.com/[[not-a-link]]
```

Tilde fence:
~~~yaml
key: [[yaml-not-a-link]]
~~~

Indented block:

    [[indented-not-a-link]]
    foo bar

- Bullet with [[bullet-link]]
    - Nested bullet with [[nested-bullet-link]]
"""
    links = wiki_links_in(doc)
    assert links == [
        "bullet-link",
        "nested-bullet-link",
        "summary-link",
        "valid-link-1",
        "valid-link-2",
    ], f"Unexpected links: {links}"


def test_flow_mapping_parser():
    fm_text = """---
title: Parser Test
generated: { by: "claude-code", at: "2026-11-02T18:00:00Z" }
comma_val: { by: "user, with comma", at: "2026-11-02" }
verified_flow_list:
  - { by: "human:alice", at: "2026-11-02", sha: "sha1" }
  - { by: "human:bob", at: "2026-11-03", sha: "sha2" }
verified_block:
  by: "human:charlie"
  sha: "sha3"
atoms:
  - "decision: ok"
---
Body text.
"""
    parsed, body = parse_frontmatter(fm_text)
    assert parsed is not None
    assert parsed["title"] == "Parser Test"
    assert parsed["generated"] == {"by": "claude-code", "at": "2026-11-02T18:00:00Z"}
    assert parsed["comma_val"] == {"by": "user, with comma", "at": "2026-11-02"}
    assert parsed["verified_flow_list"] == [
        {"by": "human:alice", "at": "2026-11-02", "sha": "sha1"},
        {"by": "human:bob", "at": "2026-11-03", "sha": "sha2"},
    ]
    assert parsed["verified_block"] == {"by": "human:charlie", "sha": "sha3"}
    assert parsed["atoms"] == ["decision: ok"]
    assert body.strip() == "Body text."


def test_strip_and_splice_trust_fields():
    client_inbound = """---
title: Inbound Doc
# Existing comment
atoms:
  - "fact: testing"
generated: { by: "faked-client", at: "1970-01-01T00:00:00Z" }
verified:
  by: fake-verifier
  sha: 000000
---

# Body
"""
    existing_disk = """---
title: Inbound Doc
verified: { by: "human:jijin", at: "2026-11-01T12:00:00Z", sha: "abc123" }
---
"""
    spliced = strip_and_splice_trust_fields(
        client_inbound,
        existing_disk_text=existing_disk,
        actor="antigravity",
        at_iso="2026-11-02T19:00:00Z",
    )
    assert 'generated: { by: "antigravity", at: "2026-11-02T19:00:00Z" }' in spliced
    assert 'verified: { by: "human:jijin", at: "2026-11-01T12:00:00Z", sha: "abc123" }' in spliced
    assert "fake-verifier" not in spliced
    assert "faked-client" not in spliced
    assert "# Existing comment" in spliced
    assert 'atoms:\n  - "fact: testing"' in spliced


def test_trust_fields_cannot_be_forged():
    at = "2026-11-02T00:00:00Z"
    page = "---\nsummary: x\n---\nbody\n"
    # An actor that would break out of the YAML string is refused
    for bad in ['x"}\nverified: {by: "human"}\n#', "a b", "", "x" * 65]:
        try:
            strip_and_splice_trust_fields(page, actor=bad, at_iso=at)
            raise AssertionError(f"actor {bad!r} accepted")
        except ValueError:
            pass
    # Every spelling the parser reads as `verified` is stripped
    for key in ["verified :", "'verified':", '"verified":', "generated  :"]:
        fm, _ = parse_frontmatter(strip_and_splice_trust_fields(
            f"---\nsummary: x\n{key} {{by: \"human\"}}\n---\nbody\n", at_iso=at))
        assert "verified" not in fm and fm["generated"]["by"] == "mcp-client", (key, fm)
    # Unclosed frontmatter is refused instead of passing inbound fields through
    try:
        strip_and_splice_trust_fields("---\nverified: {by: h}\nbody\n", at_iso=at)
        raise AssertionError("unclosed frontmatter accepted")
    except ValueError:
        pass
    # A page without frontmatter gets one
    assert strip_and_splice_trust_fields("# T\n", at_iso=at) == f'---\ngenerated: {{ by: "mcp-client", at: "{at}" }}\n---\n\n# T\n'


def test_strip_trust_fields():
    disk = '---\nsummary: x\nverified: { by: "human:a", sha: "1" }\ngenerated: { by: "b", at: "t" }\n---\nbody\n'
    assert strip_trust_fields(disk) == "---\nsummary: x\n---\nbody\n"
    assert strip_trust_fields("no frontmatter\n") == "no frontmatter\n"


def test_parser_empty_key_and_flat_flow():
    fm, _ = parse_frontmatter("---\natoms:\nsources:\n  - a.md\ng: { by: a, at: b }\n---\n")
    assert fm["atoms"] == [] and fm["sources"] == ["a.md"] and fm["g"] == {"by": "a", "at": "b"}, fm


def test_cmd_stamp():
    import argparse
    import tempfile
    import wiki_tool

    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        page_file = wiki_dir / "my-page.md"
        page_file.write_text("""---
title: My Page
verified: { by: "human:jijin", at: "2026-11-01", sha: "111" }
---

# Content
""")
        (wiki_dir / "broken.md").write_text("---\ntitle: no close\n")
        saved = wiki_tool.WIKI
        wiki_tool.WIKI = wiki_dir
        try:
            assert cmd_stamp(argparse.Namespace(pages=["wiki/my-page.md"], by="test-local")) == 0
            assert cmd_stamp(argparse.Namespace(pages=["my-page"], by='x"\nverified: {}')) == 1
            assert cmd_stamp(argparse.Namespace(pages=["broken", "missing"], by="local")) == 1
        finally:
            wiki_tool.WIKI = saved
        updated = page_file.read_text()
        assert 'verified: { by: "human:jijin", at: "2026-11-01", sha: "111" }' in updated
        assert 'generated: { by: "test-local"' in updated
        assert (wiki_dir / "broken.md").read_text() == "---\ntitle: no close\n"


def test_page_hash():
    base = "---\ntitle: Foo\n---\n\n# Foo\n\nSome body text.\n"
    h1 = page_hash(base)
    assert len(h1) == 16
    # Trust fields do not alter hash
    with_gen = "---\ntitle: Foo\ngenerated: { by: \"agent\", at: \"2026-11-01\" }\n---\n\n# Foo\n\nSome body text.\n"
    assert page_hash(with_gen) == h1
    with_ver = "---\ntitle: Foo\nverified: { by: \"human:jijin\", at: \"2026-11-01\", sha: \"abc\" }\n---\n\n# Foo\n\nSome body text.\n"
    assert page_hash(with_ver) == h1
    # CRLF normalized to LF
    with_crlf = "---\r\ntitle: Foo\r\n---\r\n\r\n# Foo\r\n\r\nSome body text.\r\n"
    assert page_hash(with_crlf) == h1
    # Trailing newlines normalized to single newline
    assert page_hash(base.rstrip("\n")) == h1
    assert page_hash(base + "\n\n\n") == h1
    # Actual content difference produces different hash
    diff = "---\ntitle: Foo\n---\n\n# Foo\n\nDifferent text.\n"
    assert page_hash(diff) != h1


def test_set_verified_field():
    base = "---\ntitle: Foo\ngenerated: { by: \"agent-1\", at: \"2026-11-01\" }\n---\n\n# Foo\n"
    stamped = set_verified_field(base, by="human:reviewer", at="2026-11-02T12:00:00Z", sha="1234567890abcdef")
    assert 'generated: { by: "agent-1", at: "2026-11-01" }' in stamped
    assert 'verified: { by: "human:reviewer", at: "2026-11-02T12:00:00Z", sha: "1234567890abcdef" }' in stamped

    # Overwrites existing verified
    updated = set_verified_field(stamped, by="human:second", at="2026-11-03T12:00:00Z", sha="fedcba0987654321")
    assert 'by: "human:reviewer"' not in updated
    assert 'verified: { by: "human:second", at: "2026-11-03T12:00:00Z", sha: "fedcba0987654321" }' in updated
    assert 'generated: { by: "agent-1"' in updated

    # Rejects invalid actor
    try:
        set_verified_field(base, by="bad actor with spaces", at="now", sha="123")
        raise AssertionError("invalid actor accepted")
    except ValueError as e:
        assert "Invalid actor" in str(e)


def test_human_actor():
    assert human_actor("jijin") == "human:jijin"
    assert human_actor("human:jijin") == "human:jijin"
    assert human_actor(None) == human_actor("") == human_actor("human") == "human"


def test_cmd_hash_and_cmd_verify():
    import argparse
    import tempfile
    import wiki_tool

    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        page_file = wiki_dir / "test-page.md"
        page_file.write_text("---\ntitle: Test\n---\n\n# Content\n")

        saved = wiki_tool.WIKI
        wiki_tool.WIKI = wiki_dir
        try:
            # Test cmd_hash
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cmd_hash(argparse.Namespace(page="test-page"))
            assert rc == 0
            expected_h = page_hash(page_file.read_text())
            assert out.getvalue().strip() == expected_h

            # Test cmd_hash missing
            assert cmd_hash(argparse.Namespace(page="missing")) == 1

            # Test cmd_verify
            rc = cmd_verify(argparse.Namespace(pages=["test-page.md"], by="jijin"))
            assert rc == 0
            content = page_file.read_text()
            assert f'verified: {{ by: "human:jijin", at:' in content
            assert f'sha: "{expected_h}"' in content

            # The default reviewer is plain `human`, as on the server
            assert cmd_verify(argparse.Namespace(pages=["test-page"], by="human")) == 0
            assert 'verified: { by: "human", at:' in page_file.read_text()

            # Test cmd_verify invalid actor
            assert cmd_verify(argparse.Namespace(pages=["test-page"], by="bad actor")) == 1
        finally:
            wiki_tool.WIKI = saved


def test_cmd_lint_verified_rule_v():
    import argparse
    import tempfile
    import wiki_tool

    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        index_file = wiki_dir / "index.md"
        index_file.write_text("# Index\n\n- [[verified-page]] — A verified page\n")

        page_file = wiki_dir / "verified-page.md"
        content = "---\ntitle: Verified Page\nsummary: Summary\nlast_updated: 2026-11-01\nsource_count: 0\nprovenance: \"test notes\"\natoms:\n  - Atom fact\n---\n\n## Section\n\nContent here.\n"
        h = page_hash(content)
        ver_content = set_verified_field(content, by="human:tester", at="2026-11-01T10:00:00Z", sha=h)
        page_file.write_text(ver_content)

        saved_wiki = wiki_tool.WIKI
        saved_index = wiki_tool.INDEX
        wiki_tool.WIKI = wiki_dir
        wiki_tool.INDEX = index_file
        try:
            # Unmodified page: Rule V produces no warnings
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cmd_lint(argparse.Namespace(strict=True, limit=20, all=False))
            assert "Stale verification" not in out.getvalue()

            # Modify page content without updating verification
            tampered = ver_content + "\nExtra unstamped modifications.\n"
            page_file.write_text(tampered)

            # Strict mode: Rule V is reported as a soft warning, not blocking (rc == 0)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cmd_lint(argparse.Namespace(strict=True, limit=20, all=False))
            assert rc == 0
            assert "Stale verification" in out.getvalue()
            assert "content changed since review by human:tester on 2026-11-01T10:00:00Z" in out.getvalue()
            assert f"sha {h} !=" in out.getvalue()
        finally:
            wiki_tool.WIKI = saved_wiki
            wiki_tool.INDEX = saved_index


def test_page_types_and_canonical():
    assert PAGE_TYPES == {"Note", "Runbook", "Decision", "Reference", "Session", "Todo"}
    for raw, expected in [
        ("note", "Note"),
        ("NOTE", "Note"),
        ("Note", "Note"),
        ("runbook", "Runbook"),
        ("Runbook", "Runbook"),
        ("RUNBOOK", "Runbook"),
        ("decision", "Decision"),
        ("reference", "Reference"),
        ("session", "Session"),
        ("todo", "Todo"),
        ("TODO", "Todo"),
        ("Todo", "Todo"),
    ]:
        assert canonical_page_type(raw) == expected, f"{raw} -> {expected}"
    for invalid in [None, "", "unknown", "guide", 123]:
        assert canonical_page_type(invalid) is None


def test_infer_page_type():
    assert infer_page_type("2026-11-02-session-notes") == "Session"
    assert infer_page_type("2026-10-30") == "Session"
    assert infer_page_type("llmwiki-todo") == "Todo"
    assert infer_page_type("backup-todo") == "Todo"
    assert infer_page_type("todos") == "Todo"
    assert infer_page_type("runbook-k8s-node-drain") == "Runbook"
    assert infer_page_type("restart-runbook") == "Runbook"
    assert infer_page_type("decision-sqlite-storage") == "Decision"
    assert infer_page_type("adr-decision") == "Decision"
    assert infer_page_type("api-reference") == "Reference"
    assert infer_page_type("git-cheatsheet") == "Reference"
    assert infer_page_type("python-spec") == "Reference"
    assert infer_page_type("general-overview") == "Note"

    # Infer decision from atoms
    decision_doc = "---\ntitle: Arch\natoms:\n  - 'decision: use postgres over mysql'\n---\n# Arch\n"
    assert infer_page_type("arch-choice", decision_doc) == "Decision"


def test_effective_stale_after():
    import datetime

    # Explicit stale_after overrides type defaults
    e_exp = {"stale_after": "2026-12-31", "type": "Runbook", "last_updated": "2026-01-01"}
    d, reason = effective_stale_after(e_exp)
    assert d == datetime.date(2026, 12, 31)
    assert "explicit stale_after" in reason

    # Runbook default: 180 days
    e_run = {"type": "Runbook", "last_updated": "2026-01-01"}
    d, reason = effective_stale_after(e_run)
    assert d == datetime.date(2026, 1, 1) + datetime.timedelta(days=180)
    assert "180 days after 2026-01-01" in reason

    # Note default: 90 days from generated.at (when last_updated is absent)
    e_note = {"type": "Note", "generated": {"at": "2026-02-01T12:00:00Z"}}
    d, reason = effective_stale_after(e_note)
    assert d == datetime.date(2026, 2, 1) + datetime.timedelta(days=90)
    assert "90 days after 2026-02-01" in reason

    # Reference default: 365 days
    e_ref = {"type": "Reference", "last_updated": "2026-01-01"}
    d, _ = effective_stale_after(e_ref)
    assert d == datetime.date(2026, 1, 1) + datetime.timedelta(days=365)

    # Todo default: 90 days
    e_todo = {"type": "Todo", "last_updated": "2026-01-01"}
    d, _ = effective_stale_after(e_todo)
    assert d == datetime.date(2026, 1, 1) + datetime.timedelta(days=90)

    # Decision and Session do not expire
    assert effective_stale_after({"type": "Decision", "last_updated": "2026-01-01"})[0] is None
    assert effective_stale_after({"type": "Session", "last_updated": "2026-01-01"})[0] is None

    # Archived / deprecated status do not expire
    assert effective_stale_after({"type": "Runbook", "status": "archived", "last_updated": "2020-01-01"})[0] is None
    assert effective_stale_after({"type": "Runbook", "status": "deprecated", "last_updated": "2020-01-01"})[0] is None

    # The clock runs from the latest of last_updated, generated.at (any edit) and verified.at (human review)
    old = {"type": "Note", "last_updated": "2025-01-01"}
    assert effective_stale_after({**old, "generated": {"at": "2026-11-01T10:00:00Z"}})[0] == datetime.date(2027, 1, 30)
    assert effective_stale_after({**old, "verified": {"at": "2026-11-01T10:00:00Z", "sha": "x"}})[0] == datetime.date(2027, 1, 30)
    assert effective_stale_after({**old, "generated": {"at": "2024-01-01T00:00:00Z"}})[0] == datetime.date(2025, 4, 1)

    # Untyped and invalid-typed pages age like Notes
    assert effective_stale_after({"last_updated": "2026-01-01"})[0] == datetime.date(2026, 4, 1)
    assert effective_stale_after({"type": "howto", "last_updated": "2026-01-01"})[0] == datetime.date(2026, 4, 1)

    # An unparseable stale_after falls back to the type default (lint S reports it)
    assert effective_stale_after({"type": "Runbook", "stale_after": "next-year", "last_updated": "2026-01-01"})[0] == datetime.date(2026, 6, 30)
    assert effective_stale_after({"type": "Note"}) == (None, "no base date (last_updated, generated.at or verified.at)")


def test_set_page_type():
    doc = "---\ntitle: My Page\nsummary: Summary here.\n---\n\n# Body\n"
    updated = set_page_type(doc, "runbook")
    assert "type: Runbook\n" in updated
    assert updated.index("type: Runbook") > updated.index("summary: Summary here.")

    # Replaces existing type
    replaced = set_page_type(updated, "decision")
    assert "type: Decision\n" in replaced
    assert "type: Runbook" not in replaced

    # A page without frontmatter (legacy header) is refused, not given a type-only frontmatter
    try:
        set_page_type("# Title\n\n**Summary:** s\n", "session")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "convert-legacy" in str(e)

    # Spaced and quoted keys are found, not duplicated
    assert set_page_type("---\ntitle: T\n'type' : note\n---\nb\n", "Todo").count("type") == 1

    # Rejects invalid type
    try:
        set_page_type(doc, "invalid_type")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Invalid page type" in str(e)


def test_cmd_set_type():
    import argparse
    import tempfile
    import wiki_tool

    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        p1 = wiki_dir / "2026-11-02-debug.md"
        p1.write_text("---\ntitle: Debug Session\nsummary: Log.\n---\n\n# Debug\n")
        p2 = wiki_dir / "k8s-node.md"
        p2.write_text("---\ntitle: K8s Node\nsummary: Node guide.\n---\n\n# K8s\n")

        saved = wiki_tool.WIKI
        wiki_tool.WIKI = wiki_dir
        try:
            # Infer type on p1 (Session)
            rc = cmd_set_type(argparse.Namespace(pages=["2026-11-02-debug"], type=None, all=False))
            assert rc == 0
            assert "type: Session" in p1.read_text()

            # Set explicit type on p2
            rc = cmd_set_type(argparse.Namespace(pages=["k8s-node"], type="runbook", all=False))
            assert rc == 0
            assert "type: Runbook" in p2.read_text()

            # Invalid type returns 1
            rc = cmd_set_type(argparse.Namespace(pages=["k8s-node"], type="bogus", all=False))
            assert rc == 1

            # Backfill with --all
            rc = cmd_set_type(argparse.Namespace(pages=[], type=None, all=True))
            assert rc == 0
            assert "type: Session" in p1.read_text()
            assert "type: Runbook" in p2.read_text()

            # --all leaves a legacy page untouched and reports it
            legacy = "# Old\n\n**Summary:** s\n**Sources:** raw/a.md\n**Last updated:** 2025-01-01\n\n---\n\nbody\n"
            p3 = wiki_dir / "old.md"
            p3.write_text(legacy)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cmd_set_type(argparse.Namespace(pages=[], type=None, all=True))
            assert rc == 1 and "old.md: no frontmatter: run convert-legacy first" in err.getvalue(), err.getvalue()
            assert p3.read_text() == legacy
        finally:
            wiki_tool.WIKI = saved


def test_convert_legacy_with_type():
    legacy = """# Test Page
**Summary**: Test summary.
**Sources**: `raw/source.md`
**Last updated**: 2026-11-01

Content here.
"""
    new_text, info = convert_legacy(legacy, slug="runbook-deploy")
    assert "type: Runbook" in new_text
    assert 'title: "Test Page"' in new_text


def test_cmd_lint_type_and_staleness():
    import argparse
    import datetime
    import tempfile
    import wiki_tool

    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        index_file = wiki_dir / "index.md"
        index_file.write_text("# Index\n\n- [[page-no-type]] — test\n- [[page-stale]] — test\n- [[page-bad-date]] — test\n- [[page-old-untyped]] — test\n")
        long_ago = (datetime.date.today() - datetime.timedelta(days=400)).isoformat()
        (wiki_dir / "page-bad-date.md").write_text(
            "---\ntitle: Bad\ntype: Decision\nstale_after: someday\nsummary: Test.\nlast_updated: 2026-11-01\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom\n---\n\n# Bad\n")
        (wiki_dir / "page-old-untyped.md").write_text(
            f"---\ntitle: Old\nsummary: Test.\nlast_updated: {long_ago}\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom\n---\n\n# Old\n")

        # Page missing type
        p1 = wiki_dir / "page-no-type.md"
        p1.write_text("---\ntitle: No Type\nsummary: Test.\nlast_updated: 2026-11-01\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom\n---\n\n# No Type\n")

        # Stale page: Runbook older than 180 days
        p2 = wiki_dir / "page-stale.md"
        old_date = (datetime.date.today() - datetime.timedelta(days=200)).isoformat()
        p2.write_text(f"---\ntitle: Stale Runbook\ntype: Runbook\nsummary: Test.\nlast_updated: {old_date}\nsource_count: 0\nprovenance: notes\natoms:\n  - Atom\n---\n\n# Stale\n")

        saved_wiki = wiki_tool.WIKI
        saved_index = wiki_tool.INDEX
        wiki_tool.WIKI = wiki_dir
        wiki_tool.INDEX = index_file
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cmd_lint(argparse.Namespace(strict=True, limit=20, all=False))
            # Strict mode: T and S are soft warnings, so rc is 0!
            assert rc == 0
            val = out.getvalue()
            assert "T. Page types (missing or invalid type)" in val
            assert "page-no-type.md — missing frontmatter 'type'" in val
            assert "S. Stale pages (past stale_after date)" in val
            assert "page-stale.md — expired on" in val
            assert "page-bad-date.md — invalid stale_after 'someday'" in val, val
            assert "page-old-untyped.md — expired on" in val and "Note default" in val, val
        finally:
            wiki_tool.WIKI = saved_wiki
            wiki_tool.INDEX = saved_index


def test_unmask_code():
    doc = """# Header

```python
# Code block with [[wiki-link]]
def foo():
    return True
```

Inline `code with [[link]]` here.

    4-space indented block
    with [[another-link]]

Body text [[normal-link]].
"""
    masked, tokens = mask_code(doc)
    assert "[[wiki-link]]" not in masked
    assert "[[another-link]]" not in masked
    assert "[[normal-link]]" in masked
    restored = unmask_code(masked, tokens)
    assert restored == doc


def test_rewrite_wiki_link():
    import re
    def rw(s: str) -> str:
        return re.sub(r"\[\[([^\]]+)\]\]", rewrite_wiki_link, s)

    # Simple page
    assert rw("[[my-page]]") == "[my-page](/my-page.md)"
    # Piped display
    assert rw("[[my-page|Custom Title]]") == "[Custom Title](/my-page.md)"
    # Page with anchor
    assert rw("[[my-page#heading]]") == "[my-page](/my-page.md#heading)"
    # Page with anchor and custom display
    assert rw("[[my-page#heading|Section Display]]") == "[Section Display](/my-page.md#heading)"
    # Same-page anchor
    assert rw("[[#anchor]]") == "[anchor](#anchor)"
    assert rw("[[#anchor|See anchor]]") == "[See anchor](#anchor)"
    # Subdirectory page
    assert rw("[[log/2026-11]]") == "[log/2026-11](/log/2026-11.md)"
    # Broken link retained
    assert rw("[[missing-page]]") == "[missing-page](/missing-page.md)"


def test_transform_frontmatter_okf():
    # summary -> description, ISO timestamps, atoms and aliases preserved
    fm_lines = [
        "title: My Runbook\n",
        "type: Runbook\n",
        "summary: How to restore database.\n",
        "stale_after: 2026-12-31\n",
        'generated: { by: "claude", at: "2026-11-01" }\n',
        "verified:\n",
        "  by: human:jijin\n",
        "  at: 2026-11-01\n",
        "  sha: abc123def\n",
        "atoms:\n",
        "  - atom 1\n",
        "aliases:\n",
        "  - db-restore\n",
    ]
    transformed = transform_frontmatter_okf(fm_lines, is_index=False)
    joined = "".join(transformed)
    assert "description: How to restore database." in joined
    assert "summary:" not in joined
    assert "stale_after: 2026-12-31T00:00:00Z" in joined
    assert 'at: "2026-11-01T00:00:00Z"' in joined
    assert "at: 2026-11-01T00:00:00Z" in joined
    assert "atoms:\n  - atom 1\n" in joined
    assert "aliases:\n  - db-restore\n" in joined

    # Root index gets okf_version: "0.2"
    idx_fm = ["title: Index\n"]
    transformed_idx = transform_frontmatter_okf(idx_fm, is_index=True)
    assert transformed_idx[0] == 'okf_version: "0.2"\n'


def test_export_okf_page():
    # Page with code fences, inline spans, frontmatter and links
    doc = """---
title: Sample Page
type: Runbook
summary: Sample summary.
stale_after: 2026-11-02
atoms:
  - atom fact
---

# Sample Page

See [[target-page|Target Page]] and [[topic#section]].

```python
# Code with [[inner-link]]
x = 1
```

Inline `[[not-a-link]]` preserved.
"""
    exported = export_okf_page(doc, is_index=False)
    assert "description: Sample summary." in exported
    assert "stale_after: 2026-11-02T00:00:00Z" in exported
    assert "[Target Page](/target-page.md)" in exported
    assert "[topic](/topic.md#section)" in exported
    assert "[[inner-link]]" in exported  # Code fence preserved
    assert "`[[not-a-link]]`" in exported  # Inline code preserved

    # Index page without frontmatter gets frontmatter with okf_version: "0.2"
    idx_raw = "# Index\n\n- [[sample]] — sample desc\n"
    idx_exported = export_okf_page(idx_raw, is_index=True)
    assert idx_exported.startswith('---\nokf_version: "0.2"\n---\n\n# Index')
    assert "[sample](/sample.md)" in idx_exported


def test_export_okf_bundle_and_cli():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        wiki_dir = Path(tmp) / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "index.md").write_text("# Index\n\n- [[page-one]] — Note\n")
        (wiki_dir / "page-one.md").write_text(
            "---\ntitle: Page One\ntype: Note\nsummary: Page one summary.\nlast_updated: 2026-11-01\nsource_count: 0\nprovenance: notes\natoms:\n  - Fact 1\n---\n\n# Page One\n\nSee [[page-two|Page Two]].\n"
        )
        sub_log = wiki_dir / "log"
        sub_log.mkdir()
        (sub_log / "2026-11.md").write_text("# Log 2026-11\n\n- 2026-11-01: [[page-one]] created.\n")

        # 1. export_okf_bundle into target dir
        out_dir = Path(tmp) / "export"
        bundle = export_okf_bundle(wiki_dir, output_dir=out_dir)
        assert len(bundle) == 3
        assert "index.md" in bundle
        assert "page-one.md" in bundle
        assert "log/2026-11.md" in bundle

        # Files written to disk
        assert (out_dir / "index.md").exists()
        assert (out_dir / "page-one.md").exists()
        assert (out_dir / "log" / "2026-11.md").exists()

        p1_text = (out_dir / "page-one.md").read_text()
        assert "description: Page one summary." in p1_text
        assert "[Page Two](/page-two.md)" in p1_text

        # 2. CLI cmd_export_okf
        saved_wiki = wiki_tool.WIKI
        wiki_tool.WIKI = wiki_dir
        try:
            cli_out = Path(tmp) / "cli_export"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cmd_export_okf(argparse.Namespace(output=str(cli_out), json=False))
            assert rc == 0
            assert "Exported 3 pages" in out.getvalue()
            assert (cli_out / "index.md").exists()

            # CLI with --json
            json_out = io.StringIO()
            with contextlib.redirect_stdout(json_out):
                rc = cmd_export_okf(argparse.Namespace(output=None, json=True))
            assert rc == 0
            data = json.loads(json_out.getvalue())
            assert data["okf_version"] == "0.2"
            assert data["page_count"] == 3
            assert "page-one.md" in data["files"]
        finally:
            wiki_tool.WIKI = saved_wiki


if __name__ == "__main__":
    test_code_line_indices_fences()
    test_code_line_indices_indented_blocks_and_lists()
    test_mask_code_line_count()
    test_body_start()
    test_wiki_links_in_list_continuation()
    test_wiki_links_in_ignores_code()
    test_flow_mapping_parser()
    test_strip_and_splice_trust_fields()
    test_trust_fields_cannot_be_forged()
    test_strip_trust_fields()
    test_parser_empty_key_and_flat_flow()
    test_cmd_stamp()
    test_page_hash()
    test_set_verified_field()
    test_human_actor()
    test_cmd_hash_and_cmd_verify()
    test_cmd_lint_verified_rule_v()
    test_page_types_and_canonical()
    test_infer_page_type()
    test_effective_stale_after()
    test_set_page_type()
    test_cmd_set_type()
    test_convert_legacy_with_type()
    test_cmd_lint_type_and_staleness()
    test_unmask_code()
    test_rewrite_wiki_link()
    test_transform_frontmatter_okf()
    test_export_okf_page()
    test_export_okf_bundle_and_cli()
    print("All wiki_tool masker tests passed!")
