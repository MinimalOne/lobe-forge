"""Tests for wiki_tool code masking, fence parsing, and wiki_links_in."""
import sys
from pathlib import Path

# Add templates to sys.path
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
if str(TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(TEMPLATES_DIR))

from wiki_tool import code_line_indices, mask_code, unmask_code, wiki_links_in


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
        "    indented code block after list",
        "    more code",
    ]
    code = code_line_indices(doc)
    # Indented code block at lines 2-4
    assert 2 in code and 3 in code and 4 in code
    # Paragraphs and list items should NOT be code
    assert 0 not in code
    assert 6 not in code
    assert 8 not in code
    assert 9 not in code
    assert 10 not in code
    assert 11 not in code
    # Indented code block at lines 13-14
    assert 13 in code and 14 in code


def test_mask_code_roundtrip_and_line_count():
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

    # Unmask must reproduce the original text identically
    restored = unmask_code(masked, tokens)
    assert restored == sample, "Roundtrip failed to restore original text!"


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


if __name__ == "__main__":
    test_code_line_indices_fences()
    test_code_line_indices_indented_blocks_and_lists()
    test_mask_code_roundtrip_and_line_count()
    test_wiki_links_in_ignores_code()
    print("All wiki_tool masker tests passed!")
