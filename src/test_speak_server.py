"""Tests for speak_server.py daemon logic.

Run: python -m pytest src/test_speak_server.py
Or:  python src/test_speak_server.py

These tests exercise the pure-logic daemon functions (word timings,
silence generation, config mapping) without requiring Piper or audio.
The piper import is lazy so these functions are testable standalone.
"""

import struct
import sys
from pathlib import Path

# Make speak_server.py importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from speak_server import _compute_word_timings, _silence_bytes


# ---------------------------------------------------------------------------
# _compute_word_timings
# ---------------------------------------------------------------------------

def test_word_timings_single_word():
    timings = _compute_word_timings("hello", 22050, 22050)
    assert len(timings) == 1
    assert timings[0][0] == "hello"
    assert timings[0][1] == 0.0
    assert timings[0][2] > 0


def test_word_timings_multiple_words_distribute_duration():
    text = "one two three"
    sample_rate = 22050
    total_samples = 22050  # 1 second
    timings = _compute_word_timings(text, total_samples, sample_rate)
    assert len(timings) == 3
    assert timings[0][0] == "one"
    assert timings[1][0] == "two"
    assert timings[2][0] == "three"
    assert timings[0][1] == 0.0
    assert timings[2][2] > 0
    assert timings[1][1] == timings[0][2]


def test_word_timings_empty_text():
    timings = _compute_word_timings("", 22050, 22050)
    assert timings == []


def test_word_timings_zero_samples():
    timings = _compute_word_timings("hello", 0, 22050)
    assert timings == []


def test_word_timings_proportional_by_char_count():
    text = "a bb"
    sample_rate = 22050
    total_samples = 22050
    timings = _compute_word_timings(text, total_samples, sample_rate)
    assert len(timings) == 2
    word_a_ms = timings[0][2] - timings[0][1]
    word_bb_ms = timings[1][2] - timings[1][1]
    assert word_bb_ms > word_a_ms


def test_word_timings_start_at_zero():
    timings = _compute_word_timings("first second", 22050, 22050)
    assert timings[0][1] == 0.0


def test_word_timings_contiguous():
    timings = _compute_word_timings("a b c", 22050, 22050)
    for i in range(len(timings) - 1):
        assert timings[i][2] == timings[i + 1][1]


# ---------------------------------------------------------------------------
# _silence_bytes
# ---------------------------------------------------------------------------

def test_silence_bytes_correct_length():
    sample_rate = 22050
    duration = 0.5
    silence = _silence_bytes(sample_rate, duration)
    expected_samples = int(sample_rate * duration)
    assert len(silence) == expected_samples * 2  # 16-bit = 2 bytes/sample


def test_silence_bytes_all_zeros():
    silence = _silence_bytes(22050, 0.1)
    samples = struct.unpack(f"<{len(silence) // 2}h", silence)
    assert all(s == 0 for s in samples)


def test_silence_bytes_zero_duration():
    silence = _silence_bytes(22050, 0.0)
    assert silence == b""


def test_silence_bytes_negative_duration():
    silence = _silence_bytes(22050, -1.0)
    assert silence == b""


# ---------------------------------------------------------------------------
# Runner for manual execution (python src/test_speak_server.py)
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