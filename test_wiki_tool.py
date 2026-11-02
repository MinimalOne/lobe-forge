"""Tests for wiki_tool code masking, fence parsing, and wiki_links_in."""
import sys
from pathlib import Path

# Add templates to sys.path
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
if str(TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATES_DIR))

from wiki_tool import (
    body_start,
    cmd_stamp,
    code_line_indices,
    mask_code,
    parse_frontmatter,
    strip_and_splice_trust_fields,
    strip_trust_fields,
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
    print("All wiki_tool masker tests passed!")
