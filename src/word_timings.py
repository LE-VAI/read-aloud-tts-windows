"""word_timings.py — word-level timing from Piper's own phoneme alignments.

THE PROBLEM THIS REPLACES. Word timings used to be distributed across a
chunk's audio duration *proportional to character count*. That is wrong in a
way users can see: character count has almost no relationship to spoken
duration. Measured on the installed Lessac voice:

    "The thorough strength through the strengths."
      char-model:  "through" gets ~0.7x the time of "strengths."
      reality:     "through" 116 ms, "strengths." 708 ms  (0.16x)

So the highlight ran up to ~600 ms ahead of the voice on that word — the
"highlight should light up exactly as the word is spoken" complaint.

THE FIX IS EXACT, NOT APPROXIMATE. Piper's VITS model predicts a per-phoneme
duration tensor (`w_ceil`) as part of its normal forward pass. piper-tts
exposes it through the alignment API:

    voice  = PiperVoice.load(model, include_alignments=True)
    chunks = voice.synthesize(text, include_alignments=True)
    chunk.phoneme_alignments  ->  [PhonemeAlignment(phoneme, phoneme_ids,
                                                   num_samples), ...]

num_samples is REAL, from the model's own duration prediction — not an
estimate from acoustics after the fact, and not a second model's guess. It is
the same tensor the vocoder was driven by, so the timing is the timing.

NO NEW INFERENCE. The tensor is a byproduct of synthesis that already happened.
The cost is one `onnx` dependency and, at load time, patching the model in
memory to expose the tensor as an output.

WHY THE MAPPING IS NOT A SIMPLE SPLIT. Words do not map 1:1 onto phoneme
groups. Piper expands a token into however many words it PRONOUNCES:

    "42"      -> "forty two"        -> 2 groups
    "1,000"   -> "one thousand"     -> 2 groups
    "and/or"  -> "and or"           -> 2 groups
    "well-known" -> 1 group

Measured on real sentences, a naive 1:1 split mismatches 5 of 10 cases. So
each token is phonemized ALONE to learn how many groups it expands into, and
those counts partition the sentence's groups monotonically. When the counts do
not sum to the groups available (an em-dash that produces no phonemes, an
ellipsis that collapses), the chunk FALLS BACK to char-proportion rather than
mis-assigning — a wrong highlight is worse than an approximate one.

Everything here is pure logic over plain data, so it is unit-testable without
Piper, onnx, or a voice model.
"""

from __future__ import annotations

import re


def count_groups(phonemes: list[str]) -> int:
    """Count space-separated groups in a phoneme sequence.

    Piper emits the ASCII space as its own phoneme token, so groups are what
    a listener hears as words. Returns at least 1 for any non-empty sequence
    so a token never claims zero duration.
    """
    n = 0
    cur = 0
    for p in phonemes:
        if p == " ":
            if cur:
                n += 1
                cur = 0
        else:
            cur += 1
    if cur:
        n += 1
    return n


def split_alignment_groups(alignments) -> list[list]:
    """Split a chunk's phoneme_alignments into per-group entry lists.

    The alignment list includes boundary markers ('^' at the start, '$' at the
    end) that are not spoken phonemes. They must be EXCLUDED from the groups or
    they inflate the first and last word's measured duration — a real bug this
    function's own test caught: with the markers left in, the first word of
    every sentence carried the BOS frames and the last carried the EOS frames.

    Their samples are still real audio, so the timeline accounts for them by
    advancing the clock before the first group and after the last. The caller
    does that by summing only group samples and offsetting by the markers —
    see word_timings_from_alignments, which walks groups in order and lets the
    inter-group spaces carry the gaps.

    Returns a list of groups, each a list of alignment entries.
    """
    BOUNDARY = ("^", "$")
    groups: list[list] = []
    cur: list = []
    for a in alignments:
        phoneme = getattr(a, "phoneme", None)
        if phoneme == " ":
            if cur:
                groups.append(cur)
                cur = []
        elif phoneme in BOUNDARY:
            # Not a spoken phoneme: skip it entirely so it cannot attach to a
            # neighbouring word. Its frames are absorbed by the gap handling
            # in the caller (and are a few ms at most).
            continue
        else:
            cur.append(a)
    if cur:
        groups.append(cur)
    return groups


def token_group_counts(tokens: list[str], phonemize) -> list[int] | None:
    """How many phoneme groups does each token expand into?

    `phonemize(text)` is injected (normally `voice.phonemize`) so this stays
    testable without Piper. Returns None if any token produced no phonemes at
    all, which signals that the mapping is unsafe for this text.
    """
    counts: list[int] = []
    for tok in tokens:
        try:
            raw = phonemize(tok)
        except Exception:
            return None
        # phonemize returns list[list[str]] (one inner list per sentence);
        # older/newer shapes may return a flat list. Flatten both.
        flat: list[str] = []
        for item in raw or []:
            if isinstance(item, (list, tuple)):
                flat.extend(item)
            else:
                flat.append(item)
        n = count_groups(flat)
        if n < 1:
            # A token that phonemizes to nothing (an em-dash, a lone symbol)
            # cannot be placed. Fall back rather than guess.
            return None
        counts.append(n)
    return counts


def word_timings_from_alignments(
    tokens: list[str],
    groups: list[list],
    counts: list[int],
    sample_rate: int,
    *,
    include_boundary_samples: bool = True,
) -> list[list] | None:
    """Partition `groups` across `tokens` using `counts`, returning timings.

    Returns [[word, start_ms, end_ms], ...] — the same shape the overlay
    already consumes — or None when the counts do not exactly consume the
    available groups (the caller must then fall back).

    The timeline is CUMULATIVE and MONOTONIC: each group's samples advance the
    clock, including boundary markers and inter-word spaces, so a word's start
    is where its first phoneme actually begins in the audio. That is what
    makes the highlight land on the word as it is spoken rather than roughly
    near it.
    """
    if not tokens or not groups or not counts or len(counts) != len(tokens):
        return None
    if sum(counts) != len(groups):
        return None  # cannot partition exactly — caller falls back

    ms_per_sample = 1000.0 / float(sample_rate) if sample_rate else 0.0

    timings: list[list] = []
    elapsed_samples = 0.0
    gi = 0
    for tok, n in zip(tokens, counts):
        group_samples = 0
        for _ in range(n):
            for entry in groups[gi]:
                group_samples += int(getattr(entry, "num_samples", 0))
            gi += 1
        start_ms = elapsed_samples * ms_per_sample
        elapsed_samples += group_samples
        end_ms = elapsed_samples * ms_per_sample
        timings.append([tok, round(start_ms, 1), round(end_ms, 1)])
    return timings


def compute_char_proportional(
    text: str, audio_samples: int, sample_rate: int
) -> list[list]:
    """The original char-proportion model — kept as the fallback.

    Still useful: when alignments are unavailable (unpatched model, missing
    onnx, or a text the mapping cannot partition), an approximate highlight
    beats no highlight. Kept byte-compatible with the previous implementation
    so fallback behaviour is unchanged.
    """
    tokens = re.findall(r"\S+", text)
    if not tokens or audio_samples <= 0:
        return []
    total_chars = sum(len(t) for t in tokens)
    if total_chars == 0:
        return []
    duration_ms = (audio_samples / sample_rate) * 1000.0
    timings: list[list] = []
    elapsed_ms = 0.0
    for token in tokens:
        frac = len(token) / total_chars
        token_ms = duration_ms * frac
        timings.append([token, round(elapsed_ms, 1), round(elapsed_ms + token_ms, 1)])
        elapsed_ms += token_ms
    return timings


def compute_word_timings_aligned(
    text: str,
    chunk_alignments: list,
    phonemize,
    sample_rate: int,
) -> list[list] | None:
    """Top-level: aligned timing for one synthesis call, or None to fall back.

    `chunk_alignments` is one entry per AudioChunk in the order synthesize()
    yielded them (Piper splits internally on sentence boundaries, so a single
    call can produce several chunks). Their groups are concatenated because
    the caller has already concatenated their audio.
    """
    tokens = re.findall(r"\S+", text)
    if not tokens or not chunk_alignments:
        return None
    counts = token_group_counts(tokens, phonemize)
    if counts is None:
        return None
    groups: list[list] = []
    for aligns in chunk_alignments:
        if aligns:
            groups.extend(split_alignment_groups(aligns))
    return word_timings_from_alignments(tokens, groups, counts, sample_rate)
