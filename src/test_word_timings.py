"""test_word_timings.py — the aligned-timing logic, without Piper.

The pure functions are tested against SYNTHETIC alignment objects, so these
run anywhere with no voice model, no onnx, and no audio. The point of most of
them is the same one the module makes: the aligned path must be dramatically
better than char-proportion where it can run, and must REFUSE to run (falling
back) rather than emit a mis-assigned highlight.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from word_timings import (  # noqa: E402
    compute_char_proportional,
    count_groups,
    split_alignment_groups,
    token_group_counts,
    word_timings_from_alignments,
)


class Align:
    """Stand-in for piper.voice.PhonemeAlignment."""

    def __init__(self, phoneme, num_samples):
        self.phoneme = phoneme
        self.num_samples = num_samples


def build_alignments(groups_samples, sr_samples_per_ms=None):
    """Build an alignment list from a list of (group_phonemes, samples) with
    single-space separators and BOS/EOS markers, mirroring Piper's output."""
    out = [Align("^", 50)]
    for i, (phonemes, samples) in enumerate(groups_samples):
        for p in phonemes:
            out.append(Align(p, samples // max(1, len(phonemes))))
        if i != len(groups_samples) - 1:
            out.append(Align(" ", 30))
    out.append(Align("$", 20))
    return out


# -- count_groups ------------------------------------------------------------

def test_count_groups_basic():
    assert count_groups(["a", "b", " ", "c"]) == 2
    assert count_groups(["a"]) == 1
    assert count_groups([]) == 0
    assert count_groups([" ", " "]) == 0


def test_count_groups_ignores_repeated_separators():
    # Piper should not emit doubled spaces, but a defensive count must not
    # invent an empty word if it does.
    assert count_groups(["a", " ", " ", "b"]) == 2


# -- split_alignment_groups --------------------------------------------------

def test_split_excludes_boundary_markers_but_keeps_samples():
    al = build_alignments([(["a", "b"], 200), (["c"], 100)])
    groups = split_alignment_groups(al)
    assert len(groups) == 2
    # The '^' and '$' entries must NOT be in any group: leaving them in
    # inflated the first and last word's duration. (This assertion originally
    # failed and caught the bug.)
    flat = [e.phoneme for g in groups for e in g]
    assert "^" not in flat, "BOS marker leaked into the first word group"
    assert "$" not in flat, "EOS marker leaked into the last word group"
    # Their frames still exist in the audio, so the full alignment sums higher
    # than the groups alone — the timeline accounts for the difference.
    assert sum(int(e.num_samples) for e in al) > sum(
        int(e.num_samples) for g in groups for e in g
    )


def test_split_handles_leading_and_trailing_spaces():
    al = [Align(" ", 10), Align("a", 20), Align(" ", 10), Align("b", 30), Align(" ", 10)]
    groups = split_alignment_groups(al)
    assert len(groups) == 2


# -- token_group_counts -----------------------------------------------------

def test_token_group_counts_learns_expansion():
    # A phonemize() that expands "42" into two words, like Piper does.
    def fake_phonemize(tok):
        if tok == "42":
            return [["f", "ɔ", "ɹ", "t", "i", " ", "t", "u"]]
        return [[tok.lower()[0]]]

    counts = token_group_counts(["Numbers", "42", "appear"], fake_phonemize)
    assert counts == [1, 2, 1]


def test_token_group_counts_returns_none_when_a_token_is_silent():
    def fake_phonemize(tok):
        if tok == "\u2014":  # em dash phonemizes to nothing
            return [[]]
        return [[tok[0]]]

    # A silent token cannot be placed -> refuse, so the caller falls back.
    assert token_group_counts(["a", "\u2014", "b"], fake_phonemize) is None


def test_token_group_counts_returns_none_on_phonemizer_error():
    def boom(_tok):
        raise RuntimeError("phonemizer unavailable")

    assert token_group_counts(["a"], boom) is None


# -- the partition ----------------------------------------------------------

def test_partition_produces_cumulative_monotonic_timings():
    # 3 words, 3 groups, known samples. sr = 1000 samples/ms for easy math.
    al = [
        Align("^", 0),
        Align("a", 100), Align(" ", 0),
        Align("b", 200), Align(" ", 0),
        Align("c", 300), Align("$", 0),
    ]
    groups = split_alignment_groups(al)
    out = word_timings_from_alignments(["A", "B", "C"], groups, [1, 1, 1], 1000)
    assert out is not None
    assert [w[0] for w in out] == ["A", "B", "C"]
    assert out[0][1] == 0.0 and out[0][2] == 100.0
    assert out[1][1] == 100.0 and out[1][2] == 300.0   # starts where A ended
    assert out[2][1] == 300.0 and out[2][2] == 600.0   # cumulative, no gaps
    # Monotonic and non-overlapping.
    for prev, nxt in zip(out, out[1:]):
        assert nxt[1] >= prev[2]


def test_partition_sums_multiple_groups_per_token():
    # "42" expands to two groups; its duration must be BOTH groups.
    al = [
        Align("^", 0),
        Align("f", 100), Align(" ", 0),
        Align("t", 150), Align(" ", 0),
        Align("x", 200), Align("$", 0),
    ]
    groups = split_alignment_groups(al)
    out = word_timings_from_alignments(["42", "X"], groups, [2, 1], 1000)
    assert out is not None
    assert out[0][1] == 0.0 and out[0][2] == 250.0, "both groups belong to '42'"
    assert out[1][1] == 250.0 and out[1][2] == 450.0


def test_partition_refuses_when_counts_do_not_match():
    # 2 groups available, 3 expected -> must return None, not guess.
    al = [Align("^", 0), Align("a", 10), Align(" ", 0), Align("b", 10), Align("$", 0)]
    groups = split_alignment_groups(al)
    assert word_timings_from_alignments(["A", "B", "C"], groups, [1, 1, 1], 1000) is None
    # Too FEW expected is also a mismatch.
    assert word_timings_from_alignments(["A"], groups, [1], 1000) is None


def test_partition_rejects_empty_inputs():
    assert word_timings_from_alignments([], [], [], 1000) is None
    assert word_timings_from_alignments(["A"], [], [1], 1000) is None
    assert word_timings_from_alignments(["A"], [["x"]], [], 1000) is None


# -- the actual improvement --------------------------------------------------

def test_aligned_beats_char_proportion_on_the_pathological_pair():
    """The real measured case: 'through' 116 ms vs 'strengths.' 708 ms.

    Char-proportion gives 'through' (7 chars) roughly 70% of 'strengths.'
    (10 chars). The truth is 16%. This test asserts the aligned path gets the
    RATIO right where the char model is off by ~4x — the whole reason for the
    change.
    """
    sample_rate = 22050
    through_ms, strengths_ms = 116, 708
    # ~22.05 samples per ms at 22050 Hz.
    al = [
        Align("^", 100),
        Align("t", int(through_ms * 22.05)), Align(" ", 50),
        Align("s", int(strengths_ms * 22.05)), Align("$", 100),
    ]
    groups = split_alignment_groups(al)
    out = word_timings_from_alignments(["through", "strengths."], groups, [1, 1], sample_rate)
    assert out is not None
    aligned_ratio = (out[0][2] - out[0][1]) / (out[1][2] - out[1][1])

    char = compute_char_proportional("through strengths.", 10_000_000, sample_rate)
    char_through = char[0][2] - char[0][1]
    char_strengths = char[1][2] - char[1][1]
    char_ratio = char_through / char_strengths

    assert 0.14 < aligned_ratio < 0.18, f"aligned ratio {aligned_ratio:.3f} should be ~0.16"
    assert char_ratio > 0.6, f"char model ratio {char_ratio:.3f} should be ~0.7"
    # The aligned result is more than 3x closer to the truth.
    truth = through_ms / strengths_ms
    assert abs(aligned_ratio - truth) < abs(char_ratio - truth) / 3


# -- char-proportion fallback (unchanged behaviour) --------------------------

def test_char_proportion_still_works_as_the_fallback():
    out = compute_char_proportional("aa bbbb", 1000, 1000)
    assert len(out) == 2
    assert out[0][1] == 0.0
    # 2 chars vs 4 chars -> one third vs two thirds of 1000 ms
    assert abs((out[0][2] - out[0][1]) - 333.3) < 1.0
    assert abs((out[1][2] - out[1][1]) - 666.7) < 1.0
    assert out[1][2] == 1000.0


def test_char_proportion_edge_cases():
    assert compute_char_proportional("", 1000, 1000) == []
    assert compute_char_proportional("   ", 1000, 1000) == []
    assert compute_char_proportional("a", 0, 1000) == []
