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

from speak import chunk_text, sanitize_text, normalize_text, find_piper_command


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