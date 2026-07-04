#!/usr/bin/env python3
"""
Long-lived TTS daemon for ReadAloudTTS.

Loads the Piper voice model ONCE on startup using the PiperVoice Python API,
then serves speak requests via stdin JSON lines. This eliminates the ~2s
model-load cold start on every Ctrl+Right-click — after the first load,
synthesis is ~tens of milliseconds (inference only, no reload).

Protocol (one JSON object per line on stdin):
  {"action": "speak", "text": "..."}           — speak the text
  {"action": "set_voice", "voice": "voice_id"} — switch voice (reloads model)
  {"action": "stop"}                           — stop current playback
  {"action": "quit"}                            — shut down the daemon

Each request gets a one-line JSON response on stdout:
  {"status": "ok", "message": "..."}    — success
  {"status": "error", "message": "..."} — failure

Reuses chunk_text, normalize_text, sanitize_text from speak.py so text
processing stays consistent with the cold-start fallback path.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import struct
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

# Make speak.py importable for shared functions.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from piper import PiperVoice, SynthesisConfig

from speak import (
    APP_DIR,
    CONFIG_PATH,
    LOG_PATH,
    chunk_text,
    load_config,
    normalize_text,
    setup_logging,
)

# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

_voice_cache: dict[str, PiperVoice] = {}
_current_voice_id: str | None = None
_playback_lock = threading.Lock()
_stop_requested = False


def _load_voice(voice_id: str) -> PiperVoice | None:
    """Load a PiperVoice model, caching it by voice_id for reuse."""
    if voice_id in _voice_cache:
        return _voice_cache[voice_id]

    config = load_config()
    voices = config.get("voices", {})
    if voice_id not in voices:
        logging.error("Voice not found: %s", voice_id)
        return None

    voice = voices[voice_id]
    model_path = APP_DIR / voice["model"]
    config_path = APP_DIR / voice["config"]
    if not model_path.exists() or not config_path.exists():
        logging.error("Voice files missing for %s", voice_id)
        return None

    logging.info("Loading Piper model for voice: %s", voice_id)
    pv = PiperVoice.load(str(model_path), config_path=str(config_path))
    _voice_cache[voice_id] = pv
    logging.info("Loaded Piper model for voice: %s", voice_id)
    return pv


def _build_syn_config(config: dict[str, Any]) -> SynthesisConfig:
    """Map config.json prosody keys to a SynthesisConfig."""
    kwargs: dict[str, Any] = {}
    if "length_scale" in config:
        kwargs["length_scale"] = float(config["length_scale"])
    if "noise_scale" in config:
        kwargs["noise_scale"] = float(config["noise_scale"])
    # Config key is noise_w; PiperVoice API field is noise_w_scale.
    if "noise_w" in config:
        kwargs["noise_w_scale"] = float(config["noise_w"])
    return SynthesisConfig(**kwargs)


def _silence_bytes(sample_rate: int, duration_s: float) -> bytes:
    """Generate N seconds of 16-bit mono silence."""
    num_samples = int(sample_rate * duration_s)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


# ---------------------------------------------------------------------------
# Word highlighting
# ---------------------------------------------------------------------------
#
# The daemon writes a highlight state file the AHK overlay polls (~30ms).
# Format is a single JSON line:
#   {"state":"start","text":"...","words":[["word",start_ms,end_ms],...]}
#   {"state":"playing","ms":1234}
#   {"state":"stop"}
#
# Word timings are *approximate* — we distribute each chunk's audio duration
# across its words proportional to character count. Piper doesn't expose
# per-word timestamps via the Python API, so this is a best-effort visual
# guide, not a karaoke-precise alignment.

_HIGHLIGHT_PATH = APP_DIR / "tmp" / "highlight_state.json"


def _compute_word_timings(text: str, audio_samples: int, sample_rate: int) -> list[list]:
    """Return [[word, start_ms, end_ms], ...] distributing audio duration by char count."""
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


def _write_highlight_state(state: dict[str, Any]) -> None:
    """Write a single-line JSON highlight state for the AHK overlay to poll."""
    try:
        _HIGHLIGHT_PATH.write_text(json.dumps(state) + "\n", encoding="utf-8")
    except OSError:
        pass


def _clear_highlight_state() -> None:
    """Remove the highlight state file."""
    try:
        _HIGHLIGHT_PATH.unlink()
    except OSError:
        pass


def _synthesize_to_wav_bytes(
    voice: PiperVoice,
    text: str,
    syn_config: SynthesisConfig,
    sentence_silence: float,
) -> tuple[bytes, int, int]:
    """Synthesize text to in-memory WAV bytes using the PiperVoice API.

    Iterates the synthesize() generator (one AudioChunk per sentence),
    concatenates audio_int16_bytes, and inserts sentence_silence between
    sentences (since SynthesisConfig has no sentence_silence field).

    Returns (wav_bytes, total_samples, sample_rate).
    """
    chunks = list(voice.synthesize(text, syn_config))
    if not chunks:
        return b"", 0, 0

    sample_rate = chunks[0].sample_rate
    silence = _silence_bytes(sample_rate, sentence_silence) if sentence_silence > 0 else b""

    # Build raw PCM by concatenating chunk audio with silence between sentences.
    pcm = io.BytesIO()
    total_samples = 0
    for i, chunk in enumerate(chunks):
        if i > 0 and silence:
            pcm.write(silence)
            total_samples += int(sample_rate * sentence_silence)
        chunk_bytes = chunk.audio_int16_bytes
        pcm.write(chunk_bytes)
        total_samples += len(chunk_bytes) // 2  # 16-bit = 2 bytes/sample
    raw_pcm = pcm.getvalue()

    # Wrap in a WAV container for winsound.PlaySound.
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wav_file:
        wav_file.setframerate(sample_rate)
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setnchannels(1)  # mono
        wav_file.writeframes(raw_pcm)
    return wav_buf.getvalue(), total_samples, sample_rate


def handle_speak(text: str, from_word: int = 0) -> dict[str, str]:
    """Speak text using the cached PiperVoice model.

    If from_word > 0, skip the first N words (used for click-to-rewind:
    the overlay sends the original text + the word index to restart from).
    """
    global _stop_requested
    _stop_requested = False
    _clear_highlight_state()

    config = load_config()
    voice_id = config.get("current_voice", _current_voice_id)
    voice = _load_voice(voice_id) if voice_id else None
    if voice is None:
        return {"status": "error", "message": f"Voice not available: {voice_id}"}

    max_chars = int(config.get("max_chars", 30000))
    chunk_chars = int(config.get("chunk_chars", 2000))
    text = normalize_text(text, max_chars)
    if not text:
        return {"status": "error", "message": "No text to speak"}

    # If from_word is set, skip the first N words (click-to-rewind).
    if from_word > 0:
        words = text.split()
        if from_word >= len(words):
            return {"status": "ok", "message": "Already at end"}
        text = " ".join(words[from_word:])
        logging.info("Seeking from word %s, remaining: %s chars", from_word, len(text))

    chunks = chunk_text(text, chunk_chars)
    syn_config = _build_syn_config(config)
    sentence_silence = float(config.get("sentence_silence", 0.5))
    inter_chunk_pause = float(config.get("inter_chunk_pause", 0.3))

    logging.info("Speaking %s chars using %s in %s chunks", len(text), voice_id, len(chunks))
    t_start = time.time()

    import winsound

    def synth_chunk(chunk_text: str) -> tuple[bytes, int, int]:
        return _synthesize_to_wav_bytes(voice, chunk_text, syn_config, sentence_silence)

    # Build word timings across all chunks for the highlight overlay.
    # Each chunk's words get timings relative to that chunk's audio, then we
    # offset them by the cumulative duration of prior chunks (+ pauses).
    all_word_timings: list[list] = []
    synth_results: list[tuple[bytes, int, int]] = []
    # Synth first chunk synchronously to measure; pipeline the rest later.
    # For highlight we need all timings up front, so synth all chunks first
    # when there aren't too many (keeps it simple and correct).
    # For large texts the pipeline still works — we just emit highlight
    # state per-chunk as we go.
    chunk_offset_ms = 0.0
    chunk_timings_list: list[list[list]] = []  # per chunk: [[word, s_ms, e_ms], ...]

    with _playback_lock:
        # Synthesize all chunks, building word timings.
        for ci, chunk in enumerate(chunks):
            if _stop_requested:
                break
            wav_bytes, total_samples, sample_rate = synth_chunk(chunk)
            synth_results.append((wav_bytes, total_samples, sample_rate))
            wtimings = _compute_word_timings(chunk, total_samples, sample_rate)
            # Offset by cumulative prior playback time.
            for wt in wtimings:
                all_word_timings.append([wt[0], round(wt[1] + chunk_offset_ms, 1), round(wt[2] + chunk_offset_ms, 1)])
            chunk_timings_list.append(wtimings)
            chunk_offset_ms += (total_samples / sample_rate * 1000.0) if sample_rate else 0
            chunk_offset_ms += inter_chunk_pause * 1000.0 if ci < len(chunks) - 1 else 0

        t_synth = time.time()
        logging.info("Synthesis took %.3fs, playback starting", t_synth - t_start)

        if _stop_requested:
            _clear_highlight_state()
            return {"status": "ok", "message": "Stopped"}

        # Start a timer thread that writes "playing" state with elapsed ms.
        # Each state write includes the full text + word timings so the AHK
        # overlay never needs to catch a transient "start" event — it can
        # pick up mid-playback and still render the highlight correctly.
        playback_start = time.time()
        total_play_ms = chunk_offset_ms

        def emit_playing() -> None:
            while not _stop_requested:
                elapsed = (time.time() - playback_start) * 1000.0
                if elapsed >= total_play_ms:
                    break
                _write_highlight_state({
                    "state": "playing",
                    "text": text,
                    "words": all_word_timings,
                    "total_ms": round(total_play_ms, 1),
                    "ms": round(elapsed, 1),
                })
                time.sleep(0.03)  # 30ms refresh
            if not _stop_requested:
                _write_highlight_state({"state": "done"})

        timer = threading.Thread(target=emit_playing, daemon=True)
        timer.start()

        # Play all chunks sequentially.
        for ci, (wav_bytes, _samples, _sr) in enumerate(synth_results):
            if _stop_requested:
                break
            winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
            if not _stop_requested and ci < len(synth_results) - 1 and inter_chunk_pause > 0:
                time.sleep(inter_chunk_pause)

        _stop_requested = True  # signal timer to stop
        timer.join(timeout=1.0)
        _write_highlight_state({"state": "done"})

    return {"status": "ok", "message": "Text spoken"}


def handle_set_voice(voice_id: str) -> dict[str, str]:
    """Switch the current voice and persist to config.json."""
    global _current_voice_id
    voice = _load_voice(voice_id)
    if voice is None:
        return {"status": "error", "message": f"Could not load voice: {voice_id}"}
    _current_voice_id = voice_id
    config = load_config()
    config["current_voice"] = voice_id
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logging.info("Voice set to %s", voice_id)
    return {"status": "ok", "message": f"Voice set to {voice_id}"}


def handle_stop() -> dict[str, str]:
    """Signal playback to stop."""
    global _stop_requested
    _stop_requested = True
    import winsound

    try:
        winsound.PlaySound(None, 0)  # Cancel any playing sound.
    except Exception:
        pass
    _write_highlight_state({"state": "stop"})
    return {"status": "ok", "message": "Stop requested"}


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

def serve() -> int:
    """Run the request-file daemon loop.

    Polls tmp/request.json every 20ms. When it appears, reads the JSON
    request, processes it, writes the response to tmp/response.json, and
    deletes the request file. This avoids the named-pipe fragility and
    lets AHK communicate via simple file I/O with ~20ms latency.
    """
    setup_logging()

    # Early exit if piper isn't importable in this interpreter. Without
    # piper the daemon can't synthesize, so there's no point starting. This
    # also prevents zombie processes when the wrong Python interpreter is
    # invoked (e.g. system Python without piper installed).
    try:
        import piper  # noqa: F401
    except ImportError:
        logging.error("piper not importable in this interpreter; exiting")
        print("speak_server: piper not installed in this interpreter", file=sys.stderr)
        return 1

    # Single-instance guard: a Windows named mutex is the correct primitive
    # here — acquisition is atomic (no race window), and the OS releases it
    # automatically when the process exits, so no cleanup is needed. PID
    # files and file locks both had race conditions on Windows.
    #
    # CRITICAL: use_last_error=True is required on the kernel32 handle so
    # ctypes.get_last_error() actually returns ERROR_ALREADY_EXISTS after
    # CreateMutexW opens an existing mutex. Without it, get_last_error()
    # always returns 0 and the guard never blocks — every StartDaemon()
    # call spawns a duplicate daemon, both read the same request.json, and
    # concurrent winsound.PlaySound calls cancel each other (silence).
    import ctypes
    from ctypes import wintypes

    ERROR_ALREADY_EXISTS = 0xB7
    _mutex_name = u"Global\\ReadAloudTTS_speak_server_singleton"
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _mutex_handle = kernel32.CreateMutexW(None, False, _mutex_name)
    last_error = ctypes.get_last_error()
    if not _mutex_handle:
        logging.error("Could not create single-instance mutex; exiting")
        return 1
    if last_error == ERROR_ALREADY_EXISTS:
        logging.info("Another speak_server daemon is already running; exiting")
        # Don't hold the handle — close it so we don't interfere with the
        # existing owner. The OS keeps the named mutex alive as long as at
        # least one process holds a handle to it.
        kernel32.CloseHandle(_mutex_handle)
        return 0

    logging.info("speak_server starting")

    # Pre-load the default voice so the first request is fast.
    config = load_config()
    voice_id = config.get("current_voice", "en_US-lessac-medium")
    global _current_voice_id
    _current_voice_id = voice_id
    _load_voice(voice_id)
    logging.info("speak_server ready (voice: %s)", voice_id)

    # Write a readiness marker so AHK knows the daemon is warm.
    ready_path = APP_DIR / "tmp" / "daemon_ready"
    ready_path.write_text("ready", encoding="utf-8")

    request_path = APP_DIR / "tmp" / "request.json"
    response_path = APP_DIR / "tmp" / "response.json"

    # Speak requests run in a worker thread so the main loop keeps polling
    # for stop/quit requests while audio is playing. Without this, a blocking
    # winsound.PlaySound call in handle_speak would prevent the daemon from
    # reading a stop request until the first speak finished — making stop
    # impossible.
    _speak_thread: threading.Thread | None = None
    _speak_lock = threading.Lock()

    def run_speak_async(text: str, from_word: int) -> dict[str, str]:
        """Run handle_speak in a background thread.

        Returns immediately with a "started" response; the actual speak
        result is logged. Stop requests are handled by the main loop which
        sets _stop_requested via handle_stop().
        """
        global _stop_requested

        def _worker() -> None:
            try:
                handle_speak(text, from_word)
            except Exception as e:
                logging.error("speak worker failed: %s", e)

        nonlocal _speak_thread
        # If a previous speak is still running, signal stop and wait for the
        # worker to exit before starting a new one. This guarantees only one
        # playback thread is alive at any time — no overlapping voices.
        if _speak_thread and _speak_thread.is_alive():
            _stop_requested = True
            try:
                import winsound as _ws
                _ws.PlaySound(None, 0)  # Cancel in-flight audio immediately.
            except Exception:
                pass
            _speak_thread.join(timeout=3.0)
        _speak_thread = threading.Thread(target=_worker, daemon=True)
        _speak_thread.start()
        return {"status": "ok", "message": "Speak started"}

    while True:
        try:
            if request_path.exists():
                try:
                    raw = request_path.read_bytes()
                    # AHK v2 FileAppend with "UTF-8" writes a 3-byte BOM
                    # (EF BB BF) at the start of the file. Python's
                    # json.loads rejects BOM-prefixed JSON with
                    # "Expecting value: line 1 column 1 (char 0)", which
                    # silently broke every request from the AHK overlay
                    # (the daemon replied "Unknown action: error" and no
                    # audio played). Strip a leading UTF-8 BOM if present.
                    if raw.startswith(b"\xef\xbb\xbf"):
                        raw = raw[3:]
                    request = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    request = {"action": "error", "message": str(e)}
                # Delete the request file immediately so it's not re-read.
                try:
                    request_path.unlink()
                except OSError:
                    pass

                action = request.get("action", "")
                if action == "quit":
                    logging.info("speak_server quitting")
                    # Stop any in-flight playback before shutting down.
                    handle_stop()
                    if _speak_thread and _speak_thread.is_alive():
                        _speak_thread.join(timeout=2.0)
                    response_path.write_text(
                        json.dumps({"status": "ok", "message": "bye"}),
                        encoding="utf-8",
                    )
                    ready_path.unlink(missing_ok=True)
                    kernel32.ReleaseMutex(_mutex_handle)
                    kernel32.CloseHandle(_mutex_handle)
                    return 0
                elif action == "speak":
                    response = run_speak_async(
                        request.get("text", ""),
                        int(request.get("from_word", 0)),
                    )
                elif action == "set_voice":
                    response = handle_set_voice(request.get("voice", ""))
                elif action == "stop":
                    response = handle_stop()
                elif action == "ping":
                    response = {"status": "ok", "message": "pong"}
                else:
                    response = {"status": "error", "message": f"Unknown action: {action}"}

                response_path.write_text(json.dumps(response), encoding="utf-8")
        except KeyboardInterrupt:
            logging.info("speak_server interrupted, shutting down")
            ready_path.unlink(missing_ok=True)
            kernel32.ReleaseMutex(_mutex_handle)
            kernel32.CloseHandle(_mutex_handle)
            return 0
        except Exception as e:
            logging.error("speak_server loop error: %s", e)

        time.sleep(0.02)  # 20ms poll interval


if __name__ == "__main__":
    raise SystemExit(serve())