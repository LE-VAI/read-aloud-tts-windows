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
# handle_set_speed — clamping, rounding, persistence guard
# ---------------------------------------------------------------------------
# The tests point CONFIG_PATH at a throwaway temp file so they never touch a
# real config.json. handle_set_speed calls load_config/save_config from the
# speak module, which reference speak.CONFIG_PATH at CALL time (module
# attribute lookup), so patching speak_server's rebinding is NOT enough —
# we patch speak.CONFIG_PATH directly.

def _speed_test_env(tmp_path, voices=None):
    """Point the config machinery at a throwaway file; return (module, config path)."""
    import json as _json

    import speak
    import speak_server

    cfg_path = tmp_path / "config.json"
    cfg = {
        "current_voice": "en_US-lessac-medium",
        "length_scale": 1.0,
        "voices": voices if voices is not None else {"en_US-lessac-medium": {}},
    }
    cfg_path.write_text(_json.dumps(cfg), encoding="utf-8")

    real_config_path = speak.CONFIG_PATH
    real_runtime = speak_server._runtime_length_scale
    speak.CONFIG_PATH = cfg_path
    return speak, speak_server, (real_config_path, real_runtime)


def _restore_speed_env(saved):
    speak, speak_server, (real_config_path, real_runtime) = saved
    speak.CONFIG_PATH = real_config_path
    speak_server._runtime_length_scale = real_runtime


def test_set_speed_clamps_high():
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td))
        try:
            speak, speak_server, _ = saved
            resp = speak_server.handle_set_speed(99.0)
            assert resp["speed"] == 2.0, resp
            assert speak_server._runtime_length_scale == 2.0
        finally:
            _restore_speed_env(saved)


def test_set_speed_clamps_low():
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td))
        try:
            speak, speak_server, _ = saved
            resp = speak_server.handle_set_speed(0.1)
            assert resp["speed"] == 0.5, resp
            assert speak_server._runtime_length_scale == 0.5
        finally:
            _restore_speed_env(saved)


def test_set_speed_rounds_to_2dp():
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td))
        try:
            speak, speak_server, _ = saved
            # 1.0 * 1.1 accumulated 3 times = 1.331 — the rounding-drift
            # case the AHK side guards against; the daemon must land on
            # a clean 2-dp value regardless of input precision.
            resp = speak_server.handle_set_speed(1.331)
            assert resp["speed"] == 1.33, resp
            assert speak_server._runtime_length_scale == 1.33
        finally:
            _restore_speed_env(saved)


def test_set_speed_persists_to_config():
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td))
        try:
            speak, speak_server, _ = saved
            speak_server.handle_set_speed(0.9)
            on_disk = speak.load_config()
            assert on_disk["length_scale"] == 0.9, on_disk
        finally:
            _restore_speed_env(saved)


def test_set_speed_no_voices_does_not_persist():
    """The persistence guard: a config with no voices = corrupt/fallback —
    persisting would write empty defaults over the user's real file."""
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td), voices={})
        try:
            speak, speak_server, _ = saved
            before = (Path(speak.CONFIG_PATH)).read_text(encoding="utf-8")
            resp = speak_server.handle_set_speed(1.5)
            # Runtime value still applied — the session isn't broken.
            assert speak_server._runtime_length_scale == 1.5
            assert resp["status"] == "ok"
            assert "session only" in resp["message"], resp
            # But the file on disk is untouched.
            after = (Path(speak.CONFIG_PATH)).read_text(encoding="utf-8")
            assert before == after
        finally:
            _restore_speed_env(saved)


def test_set_speed_labels():
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td))
        try:
            speak, speak_server, _ = saved
            assert speak_server.handle_set_speed(0.5)["message"] == "2.0x faster"
            assert speak_server.handle_set_speed(1.0)["message"] == "normal speed"
            assert speak_server.handle_set_speed(1.6)["message"] == "1.6x slower"
        finally:
            _restore_speed_env(saved)


def test_reset_speed_inverse_nudge_reaches_normal():
    """Ctrl+0 semantics: AHK ResetSpeed sends current * (1.0/current).

    The daemon's set_speed is ABSOLUTE — the AHK multiplies client-side
    (SendSpeed: newSpeed = Round(current * factor, 2)) and sends the flat
    target. Simulate that exact wire computation for each speed a user
    can nudge to (0.8 favorite, clamps, odd nudges): the AHK-side product
    must land on exactly 1.0 after its 2dp round, and the daemon must
    round-trip that to speed 1.0 on disk.
    """
    from pathlib import Path
    import tempfile

    starts = [0.8, 0.5, 2.0, 0.91, 1.1]
    with tempfile.TemporaryDirectory() as td:
        saved = _speed_test_env(Path(td))
        try:
            speak, speak_server, cfg_path = saved
            for start in starts:
                speak_server.handle_set_speed(start)
                # AHK SendSpeed math: newSpeed = Round(current * factor, 2)
                # with factor = 1.0 / current (the ResetSpeed inverse nudge).
                current = speak_server.load_config()["length_scale"]
                wire = round(current * (1.0 / current), 2)
                assert wire == 1.0, (
                    f"AHK reset math from {start}: current={current} wire={wire}"
                )
                result = speak_server.handle_set_speed(wire)
                assert result["speed"] == 1.0, (
                    f"reset from {start} via {wire} -> {result['speed']}"
                )
                on_disk = speak_server.load_config()
                assert on_disk["length_scale"] == 1.0, (
                    f"reset from {start} must persist 1.0, got {on_disk['length_scale']}"
                )
        finally:
            _restore_speed_env(saved)


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