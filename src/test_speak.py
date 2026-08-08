"""Tests for speak.py text processing functions.

Run: python -m pytest src/test_speak.py
Or:  python src/test_speak.py

These tests exercise the pure-logic functions (chunk_text, sanitize_text,
normalize_text, find_piper_command) without requiring Piper or audio playback.
"""

import sys
from pathlib import Path

# Make speak.py importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from speak import chunk_text, sanitize_text, normalize_text, normalize_markdown, find_piper_command


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------

def test_chunk_text_single_short_text():
    """A short single-paragraph text should produce exactly one chunk."""
    chunks = chunk_text("Hello world.", 2000)
    assert len(chunks) == 1
    assert chunks[0] == "Hello world."


def test_chunk_text_merges_short_paragraphs():
    """Multiple short paragraphs should be merged up to chunk_chars,
    not emitted as one chunk per paragraph (the old behavior)."""
    paragraphs = "\n\n".join(f"Paragraph {i}." for i in range(10))
    chunks = chunk_text(paragraphs, 2000)
    # 10 short paragraphs should fit in one chunk at 2000 chars.
    assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"


def test_chunk_text_splits_when_exceeding_budget():
    """Two paragraphs that together exceed chunk_chars should produce two chunks."""
    para_a = "A" * 1500
    para_b = "B" * 1500
    text = f"{para_a}\n\n{para_b}"
    chunks = chunk_text(text, 2000)
    assert len(chunks) == 2
    assert chunks[0] == para_a
    assert chunks[1] == para_b


def test_chunk_text_empty_string():
    assert chunk_text("", 2000) == []


def test_chunk_text_whitespace_only_paragraphs_ignored():
    text = "First.\n\n   \n\nSecond."
    chunks = chunk_text(text, 2000)
    assert len(chunks) == 1
    assert "First." in chunks[0]
    assert "Second." in chunks[0]


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------

def test_sanitize_text_em_dash_preserved():
    """Em-dashes should become a spaced em-dash, not a bare hyphen."""
    text = "thought\u2014wait"
    result = sanitize_text(text)
    assert "\u2014" in result, "Em-dash should be preserved as spaced em-dash"
    assert "-" not in result.replace("\u2014", ""), "Should not be a bare hyphen"


def test_sanitize_text_en_dash_to_hyphen():
    """En-dashes should become a bare hyphen (for ranges)."""
    text = "pages 5\u20137"
    result = sanitize_text(text)
    assert "5-7" in result


def test_sanitize_text_dollar_verbalized():
    text = "That costs $5."
    result = sanitize_text(text)
    assert "dollars" in result.lower()
    assert "$" not in result


def test_sanitize_text_percent_verbalized():
    text = "50% off"
    result = sanitize_text(text)
    assert "percent" in result.lower()
    assert "%" not in result


def test_sanitize_text_ampersand_verbalized():
    text = "Tom & Jerry"
    result = sanitize_text(text)
    assert " and " in result
    assert "&" not in result


def test_sanitize_text_plus_verbalized():
    text = "a+b"
    result = sanitize_text(text)
    assert "plus" in result.lower()


def test_sanitize_text_equals_verbalized():
    text = "x=y"
    result = sanitize_text(text)
    assert "equals" in result.lower()


def test_sanitize_text_at_sign_verbalized():
    text = "user@host"
    result = sanitize_text(text)
    assert " at " in result


def test_sanitize_text_smart_quotes_replaced():
    text = "\u201chello\u201d"
    result = sanitize_text(text)
    assert '"hello"' in result


def test_sanitize_text_control_chars_to_space():
    text = "a\x07b"
    result = sanitize_text(text)
    assert "\x07" not in result
    assert "a" in result and "b" in result


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------

def test_normalize_text_collapses_whitespace():
    text = "hello    world\t\tnext"
    result = normalize_text(text, 30000)
    assert "  " not in result  # no double spaces
    assert "hello world next" in result


def test_normalize_text_truncates_at_max_chars():
    text = "A" * 500
    result = normalize_text(text, 100)
    assert len(result) <= 103  # 100 + "..." 
    assert result.endswith("...")


def test_normalize_text_preserves_newlines():
    text = "para one\n\npara two"
    result = normalize_text(text, 30000)
    assert "\n\n" in result


# ---------------------------------------------------------------------------
# find_piper_command
# ---------------------------------------------------------------------------

def test_find_piper_command_uses_sys_executable():
    """Should always return [sys.executable, '-m', 'piper'], not piper.exe."""
    cmd = find_piper_command()
    assert cmd[0] == sys.executable
    assert cmd[1] == "-m"
    assert cmd[2] == "piper"


def test_find_piper_command_does_not_use_piper_exe():
    """The fragile piper.exe zip-app launcher should never be in the command."""
    cmd = find_piper_command()
    assert "piper.exe" not in " ".join(cmd)


# ---------------------------------------------------------------------------
# normalize_markdown
# ---------------------------------------------------------------------------

def test_normalize_markdown_table_converts_to_prose():
    """A markdown table should become flowing prose, not pipe-delimited fragments."""
    table = "| Cause | Fix |\n|---|---|\n| No silence. | Add 0.3s silence. |"
    result = normalize_markdown(table)
    assert "|" not in result, f"Pipes should be gone, got: {result!r}"
    assert "Cause" in result
    assert "Fix" in result
    assert "No silence." in result
    assert "Add 0.3s silence." in result


def test_normalize_markdown_table_has_context_headers():
    """Table rows should include column headers as context."""
    table = "| Cause | Fix |\n|---|---|\n| Bug A. | Fix A. |"
    result = normalize_markdown(table)
    assert "Cause:" in result or "Cause " in result
    assert "Fix:" in result or "Fix " in result


def test_normalize_markdown_table_no_isolated_cells():
    """Table cells should not appear as isolated fragments."""
    table = "| Name | Value |\n|---|---|\n| Foo | 42 |"
    result = normalize_markdown(table)
    # The result should be sentence(s), not "Name\nValue\nFoo\n42"
    # Check that headers and data are connected in the same prose block.
    assert "Name" in result
    assert "42" in result
    assert "|" not in result
    # Should not have isolated "Name" on its own line followed by "Value"
    lines = [l.strip() for l in result.strip().split("\n") if l.strip()]
    assert not any(l == "Name" for l in lines)
    assert not any(l == "Value" for l in lines)


def test_normalize_markdown_strips_bold():
    """Bold markdown (**text**) should be stripped to plain text."""
    text = "This is **bold** text."
    result = normalize_markdown(text)
    assert "**" not in result
    assert "bold" in result


def test_normalize_markdown_strips_italic():
    """Italic markdown (*text*) should be stripped to plain text."""
    text = "This is *italic* text."
    result = normalize_markdown(text)
    assert "*" not in result
    assert "italic" in result


def test_normalize_markdown_strips_inline_code():
    """Inline code (`code`) should be stripped to plain text."""
    text = "Run `pip install` to install."
    result = normalize_markdown(text)
    assert "`" not in result
    assert "pip install" in result


def test_normalize_markdown_strips_headings():
    """Heading hashes should be stripped, text preserved."""
    text = "# Main Heading\nSome content."
    result = normalize_markdown(text)
    assert "#" not in result
    assert "Main Heading" in result


def test_normalize_markdown_strips_links():
    """Links [text](url) should become just the text."""
    text = "See [the docs](https://example.com) for more."
    result = normalize_markdown(text)
    assert "https" not in result
    assert "(" not in result
    assert "the docs" in result


def test_normalize_markdown_strips_bullets():
    """Bullet lists should have their markers stripped."""
    text = "- First item\n- Second item\n- Third item"
    result = normalize_markdown(text)
    assert "- First" not in result
    assert "First item" in result
    assert "Second item" in result


def test_normalize_markdown_numbered_lists_get_ordinals():
    """Numbered lists should get ordinal words."""
    text = "1. First step.\n2. Second step.\n3. Third step."
    result = normalize_markdown(text)
    assert "First," in result
    assert "Second," in result
    assert "Third," in result


def test_normalize_markdown_preserves_plain_text():
    """Plain text without any markdown should pass through unchanged."""
    text = "This is a plain sentence with no formatting."
    result = normalize_markdown(text)
    assert result == text


def test_normalize_markdown_mixed_content():
    """A mix of markdown elements should all be handled in one pass."""
    text = """# Heading

Some **bold** and *italic* text with `code`.

| Col A | Col B |
|---|---|
| Val 1. | Val 2. |

- Bullet one
- Bullet two"""
    result = normalize_markdown(text)
    assert "|" not in result
    assert "**" not in result
    assert "`" not in result
    assert "#" not in result
    assert "Heading" in result
    assert "bold" in result
    assert "italic" in result
    assert "code" in result
    assert "Col A" in result
    assert "Val 1." in result
    assert "Bullet one" in result


def test_normalize_markdown_non_table_pipes_preserved():
    """Pipes not in table context should be preserved for sanitize_text."""
    # A lone pipe line without a following separator isn't a table.
    text = "Use pipe | as a delimiter."
    result = normalize_markdown(text)
    # Pipe should survive (sanitize_text handles it)
    assert "|" in result


def test_normalize_markdown_blockquote_stripped():
    """Blockquote markers should be stripped."""
    text = "> This is a quote.\n> Continued."
    result = normalize_markdown(text)
    assert ">" not in result
    assert "This is a quote." in result
    assert "Continued." in result


def test_normalize_markdown_horizontal_rule_removed():
    """Horizontal rules (---, ***, ___) should be removed."""
    text = "Above the line.\n---\nBelow the line."
    result = normalize_markdown(text)
    # The --- line should be gone (not turned into a table since it doesn't start with |)
    lines = [l for l in result.split("\n") if l.strip()]
    assert not any(l.strip() == "---" for l in lines)
    assert "Above the line." in result
    assert "Below the line." in result


def test_normalize_text_includes_markdown_normalization():
    """normalize_text should call normalize_markdown first."""
    # A table in the input should come out as prose.
    text = "| A | B |\n|---|---|\n| 1. | 2. |"
    result = normalize_text(text, 30000)
    assert "|" not in result
    assert "A" in result
    assert "B" in result


# ---------------------------------------------------------------------------
# Runner for manual execution (python src/test_speak.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [name for name in globals() if name.startswith("test_")]
    passed = 0
    failed = 0
    for name in sorted(tests):
        try:
            globals()[name]()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {name}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    if failed:
        sys.exit(1)