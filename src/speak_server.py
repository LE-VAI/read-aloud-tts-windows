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
import shutil
import struct
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

# Make speak.py importable for shared functions.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from speak import (
    APP_DIR,
    CONFIG_PATH,
    LOG_PATH,
    TMP_DIR,
    chunk_text,
    load_config,
    normalize_text,
    setup_logging,
)

# Piper is imported lazily (inside _load_voice / serve) so the pure-logic
# functions (_compute_word_timings, _silence_bytes, chunk_text, etc.) can
# be tested without Piper installed.
_piper_available: bool | None = None


def _ensure_piper():
    """Import Piper on first use. Returns (PiperVoice, SynthesisConfig)."""
    global _piper_available
    if _piper_available is False:
        raise ImportError("piper was not importable in this interpreter")
    try:
        from piper import PiperVoice, SynthesisConfig
        _piper_available = True
        return PiperVoice, SynthesisConfig
    except ImportError:
        _piper_available = False
        raise

# ---------------------------------------------------------------------------
# Model cache
# ---------------------------------------------------------------------------

_voice_cache: dict[str, Any] = {}
_current_voice_id: str | None = None
_playback_lock = threading.Lock()
_stop_requested = False
# Runtime speed override (length_scale). When non-None, overrides the
# config.json value so on-the-fly speed changes (Ctrl+=/Ctrl+-/Ctrl+0)
# take effect on the next chunk without waiting for config reload.
# Set by the "set_speed" action; cleared back to None by a reset (1.0
# also writes to config so it persists across restarts).
_runtime_length_scale: float | None = None
# Heartbeat stop flag — set to True on quit so the keep-alive thread
# exits cleanly instead of being killed mid-synthesize.
_heartbeat_stop = threading.Event()


def _load_voice(voice_id: str) -> Any:
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

    PiperVoice, _ = _ensure_piper()
    logging.info("Loading Piper model for voice: %s", voice_id)
    pv = PiperVoice.load(str(model_path), config_path=str(config_path))
    # Replace the bare default InferenceSession that PiperVoice.load
    # creates with one carrying tuned ONNX Runtime SessionOptions.
    #
    # Root cause of the "first request after idle is slow" regression:
    # ONNX Runtime's intra-op thread pool parks worker threads in the OS
    # scheduler after a few seconds of idle. The first inference after
    # the gap then pays thread-wake cost — documented in
    # microsoft/onnxruntime#7449. PiperVoice.load() constructs the
    # session with a bare onnxruntime.SessionOptions() and no spin
    # config, so there is nothing keeping the workers warm.
    #
    # Fix (per ORT maintainer tlh20 in #7449): set intra_op_num_threads=1.
    # This eliminates the intra-op thread pool entirely — there are no
    # worker threads to park, so there is no wake cost. ORT's tlh20 says:
    # "If the effect is still seen with 1 thread then ... there may well be
    # spin-or-block or other heuristics in the system" — i.e. CPU C-states.
    #
    # This is safe for Piper lessac-medium: the model is engineered to run
    # real-time on a Raspberry Pi 4 (4-core ARM), so a single desktop CPU
    # thread is more than enough for sub-second sentence synthesis.
    #
    # Spin config (allow_spinning, spin_duration_us=1000, spin_backoff_max=8)
    # is kept as belt-and-suspenders for any inter-op threads, but the
    # primary lever is intra_op_num_threads=1.
    try:
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.intra_op_num_threads = 1
        so.add_session_config_entry("session.intra_op.allow_spinning", "1")
        so.add_session_config_entry("session.intra_op.spin_duration_us", "1000")
        so.add_session_config_entry("session.intra_op.spin_backoff_max", "8")
        providers = ["CPUExecutionProvider"]
        pv.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=providers
        )
        logging.info(
            "Replaced Piper session with tuned ORT session "
            "(intra_op_num_threads=1, spin enabled)"
        )
    except Exception as e:
        # Don't break startup if this ORT version doesn't support the
        # config entries — log and fall back to Piper's default session.
        logging.warning("Could not install tuned ORT session (%s); using Piper default", e)
    _voice_cache[voice_id] = pv
    logging.info("Loaded Piper model for voice: %s", voice_id)
    return pv


# ---------------------------------------------------------------------------
# Keep-alive heartbeat
# ---------------------------------------------------------------------------
#
# Belt-and-suspenders defense against ONNX Runtime issue #7449: ORT's
# intra-op thread pool parks worker threads in the OS scheduler after a
# few seconds of idle, so the first inference after a gap pays thread-wake
# cost (multi-second delay). The tuned SessionOptions above enables
# bounded spin-waiting, but as a second layer we also synthesize a
# single-space utterance every 5 s and discard the audio. This keeps the
# session's thread pool active and the allocator arenas warm even during
# long idle stretches (e.g. the user not pressing Home for 30 minutes).
#
# The heartbeat synthesizes a one-space string — minimal phoneme work,
# ~5-15 ms on typical CPU, well below the 20 ms poll interval of the
# main loop, and well below the user's perception threshold. Audio is
# discarded (we never call winsound), so there's no audible artifact.

_HEARTBEAT_INTERVAL_S = 5.0
_HEARTBEAT_TEXT = " "


def _heartbeat_loop() -> None:
    """Background thread: synthesize a single space every 5s to keep ORT warm."""
    logging.info("heartbeat thread started (interval=%.1fs)", _HEARTBEAT_INTERVAL_S)
    while not _heartbeat_stop.wait(_HEARTBEAT_INTERVAL_S):
        try:
            voice_id = _current_voice_id
            if voice_id is None:
                continue
            pv = _voice_cache.get(voice_id)
            if pv is None:
                continue
            # synthesize_stream_raw yields raw int16 PCM chunks; we
            # consume and discard them. Use the cached SynthesisConfig
            # if one is available, else fall back to defaults.
            config = load_config()
            syn_config = _build_syn_config(config)
            for _chunk in pv.synthesize_stream_raw(_HEARTBEAT_TEXT, syn_config):
                pass
        except Exception as e:
            # Heartbeat failures must never kill the thread or break
            # the daemon — log and continue. The next tick will retry.
            logging.debug("heartbeat tick skipped: %s", e)
    logging.info("heartbeat thread stopping")


def _start_heartbeat() -> None:
    """Start the keep-alive heartbeat thread (daemon, dies with the process)."""
    t = threading.Thread(target=_heartbeat_loop, name="tts-heartbeat", daemon=True)
    t.start()


def _build_syn_config(config: dict[str, Any]) -> Any:
    """Map config.json prosody keys to a SynthesisConfig.

    If _runtime_length_scale is set (via on-the-fly speed control),
    it overrides the config value so speed changes take effect on
    the next chunk without a config reload.
    """
    _, SynthesisConfig = _ensure_piper()
    kwargs: dict[str, Any] = {}
    if _runtime_length_scale is not None:
        kwargs["length_scale"] = _runtime_length_scale
    elif "length_scale" in config:
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
    if num_samples <= 0:
        return b""
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
    chunk_chars = int(config.get("chunk_chars", 600))
    # Fast-start: cap the first chunk at a smaller size so audio starts
    # quickly. Chunk 0 is synthesized synchronously before playback, so
    # a full 600-char chunk means 6-13s of silence before first audio
    # (at length_scale=1.2). A 150-char first chunk = ~1-2 sentences =
    # ~1-2s to first audio. Subsequent chunks pipeline at full size.
    first_chunk_chars = int(config.get("first_chunk_chars", 150))
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

    chunks = chunk_text(text, chunk_chars, first_chunk_chars=first_chunk_chars)
    sentence_silence = float(config.get("sentence_silence", 0.5))
    inter_chunk_pause = float(config.get("inter_chunk_pause", 0.3))

    logging.info("Speaking %s chars using %s in %s chunks", len(text), voice_id, len(chunks))
    t_start = time.time()
    t_first_audio: float | None = None

    import winsound
    import tempfile
    import shutil

    def synth_chunk(chunk_text: str) -> tuple[bytes, int, int]:
        # Rebuild syn_config per chunk so on-the-fly speed changes
        # (Ctrl+Alt+[/Ctrl+Alt+]/Ctrl+Alt+\) take effect on the next
        # chunk, not only on the next speak request.
        cfg = load_config()
        syn_cfg = _build_syn_config(cfg)
        return _synthesize_to_wav_bytes(voice, chunk_text, syn_cfg, sentence_silence)

    # Pipelined synthesis + playback.
    #
    # PREVIOUS BUG: the daemon synthesized ALL chunks to completion before
    # playing even the first one. For a 3-chunk selection that meant 12-53s
    # of silence before the user heard anything — the original speak.py
    # cold-start path pipelined (synth N+1 while N plays) but that
    # optimization was lost when the daemon was built. We restore it here:
    #
    #   1. Synthesize chunk 0 synchronously (first audio must wait for it).
    #   2. Start playing chunk 0.
    #   3. While chunk 0 plays, synthesize chunk 1 in a background thread.
    #   4. When chunk 0 finishes, wait for chunk 1's synth, then play it.
    #   5. Repeat until done.
    #
    # Highlight word timings are computed per-chunk as each chunk is
    # synthesized and appended to the running list — the overlay picks
    # them up via the 30ms highlight_state poller.

    all_word_timings: list[list] = []
    chunk_offset_ms = 0.0

    # Shared pipeline state between the synth worker and the playback loop.
    # pending[ci] holds (wav_bytes, samples, sr, wtimings) once chunk ci is
    # synthesized; a threading.Event signals completion.
    pending: dict[int, tuple[bytes, int, int, list[list]]] = {}
    pending_events: dict[int, threading.Event] = {}
    synth_errors: list[str] = []

    def synth_worker(ci: int, chunk_text: str) -> None:
        try:
            wav_bytes, total_samples, sample_rate = synth_chunk(chunk_text)
            # Store RAW (un-offset) word timings; the playback loop applies
            # the offset when it consumes this chunk, because the offset
            # depends on cumulative playback time which only the playback
            # loop knows.
            wtimings = _compute_word_timings(chunk_text, total_samples, sample_rate)
            pending[ci] = (wav_bytes, total_samples, sample_rate, wtimings)
        except Exception as e:
            logging.error("Synth worker failed on chunk %d: %s", ci, e)
            synth_errors.append(str(e))
        finally:
            if ci in pending_events:
                pending_events[ci].set()

    with _playback_lock:
        playback_temp_dir = Path(tempfile.mkdtemp(prefix="playback-", dir=TMP_DIR))
        try:
            for ci, chunk in enumerate(chunks):
                if _stop_requested:
                    break

                if ci == 0:
                    # First chunk: synthesize synchronously on the playback
                    # thread so we can start audio as fast as possible.
                    wav_bytes, total_samples, sample_rate = synth_chunk(chunk)
                    wtimings = _compute_word_timings(chunk, total_samples, sample_rate)
                    for wt in wtimings:
                        wt[1] = round(wt[1] + chunk_offset_ms, 1)
                        wt[2] = round(wt[2] + chunk_offset_ms, 1)
                    all_word_timings.extend(wtimings)
                else:
                    # Wait for the background synth worker to finish chunk ci.
                    event = pending_events.get(ci)
                    if event:
                        event.wait(timeout=120.0)
                    result = pending.get(ci)
                    if result is None:
                        if synth_errors:
                            logging.error("Skipping chunk %d (synth failed): %s", ci, synth_errors[-1])
                        break
                    wav_bytes, total_samples, sample_rate, wtimings = result
                    # Apply the cumulative playback offset to this chunk's
                    # word timings (the worker stored raw timings).
                    for wt in wtimings:
                        wt[1] = round(wt[1] + chunk_offset_ms, 1)
                        wt[2] = round(wt[2] + chunk_offset_ms, 1)
                    all_word_timings.extend(wtimings)

                if _stop_requested:
                    break

                # Start the background synth for the NEXT chunk while we play.
                if ci + 1 < len(chunks):
                    pending_events[ci + 1] = threading.Event()
                    worker = threading.Thread(
                        target=synth_worker,
                        args=(ci + 1, chunks[ci + 1]),
                        daemon=True,
                    )
                    worker.start()

                # Write the WAV to a temp file and play it asynchronously.
                chunk_path = playback_temp_dir / f"play-{ci:04d}.wav"
                chunk_path.write_bytes(wav_bytes)
                chunk_duration_s = (total_samples / sample_rate) if sample_rate else 1.0

                winsound.PlaySound(str(chunk_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                if t_first_audio is None:
                    t_first_audio = time.time()
                    logging.info("First audio after %.3fs (synth of chunk 0)", t_first_audio - t_start)

                # Poll for stop or chunk completion. The buffer must account
                # for audio device startup latency (50-200ms on some drivers)
                # plus a safety margin so the last word isn't truncated by
                # the PlaySound(None, 0) cancel. 0.5s total buffer.
                poll_end = time.time() + chunk_duration_s + 0.5
                while not _stop_requested and time.time() < poll_end:
                    time.sleep(0.03)
                winsound.PlaySound(None, 0)

                # Advance the cumulative playback time for word-timing offsets.
                chunk_offset_ms += chunk_duration_s * 1000.0
                if ci < len(chunks) - 1 and inter_chunk_pause > 0 and not _stop_requested:
                    time.sleep(inter_chunk_pause)
                    chunk_offset_ms += inter_chunk_pause * 1000.0
        finally:
            winsound.PlaySound(None, 0)
            shutil.rmtree(playback_temp_dir, ignore_errors=True)

        if t_first_audio is None:
            # No audio ever started — likely stopped before chunk 0 finished.
            logging.info("Stopped before first audio; total elapsed %.3fs", time.time() - t_start)
        else:
            logging.info("All chunks played; total %.3fs", time.time() - t_start)

        _stop_requested = True  # signal any pending synth worker to stop
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


def handle_set_speed(speed: float) -> dict[str, str]:
    """Set the speaking speed (length_scale) on the fly.

    Takes effect on the next chunk being synthesized — no model reload,
    no daemon restart. Also persists to config.json so it survives
    restarts.

    Range: 0.5 (2x faster) to 2.0 (2x slower). Default 1.0.
    """
    global _runtime_length_scale
    speed = max(0.5, min(2.0, round(speed, 2)))
    _runtime_length_scale = speed
    # Persist to config so it survives restarts.
    config = load_config()
    config["length_scale"] = speed
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    logging.info("Speed set to %.2f", speed)
    # Return a human-friendly multiplier (1.0 = normal, 0.5 = 2x fast, 2.0 = 2x slow)
    if speed < 1.0:
        mult = 1.0 / speed
        label = f"{mult:.1f}x faster"
    elif speed > 1.0:
        label = f"{speed:.1f}x slower"
    else:
        label = "normal speed"
    return {"status": "ok", "message": label, "speed": speed}


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
        _ensure_piper()
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

    # Start the keep-alive heartbeat thread. Belt-and-suspenders against
    # ORT #7449 thread-pool parking during long idle stretches.
    _heartbeat_stop.clear()
    _start_heartbeat()

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
                    _heartbeat_stop.set()
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
                elif action == "set_speed":
                    response = handle_set_speed(float(request.get("speed", 1.0)))
                elif action == "stop":
                    response = handle_stop()
                elif action == "ping":
                    response = {"status": "ok", "message": "pong"}
                else:
                    response = {"status": "error", "message": f"Unknown action: {action}"}

                response_path.write_text(json.dumps(response), encoding="utf-8")
        except KeyboardInterrupt:
            logging.info("speak_server interrupted, shutting down")
            _heartbeat_stop.set()
            ready_path.unlink(missing_ok=True)
            kernel32.ReleaseMutex(_mutex_handle)
            kernel32.CloseHandle(_mutex_handle)
            return 0
        except Exception as e:
            logging.error("speak_server loop error: %s", e)

        # Self-heal the readiness marker. If anything deleted it (AHK
        # restarting and pruning stale state, a cleanup script, or a
        # transient FS issue), the daemon is still alive and ready, so
        # restore the marker immediately. Without this, AHK loses track
        # of the daemon and every subsequent Home press silently fails
        # because AHK spawns a competitor that exits with "already
        # running" — the exact overnight-desync issue users hit after
        # sleep/reboot/AHK-reload.
        if not ready_path.exists():
            try:
                ready_path.write_text("ready", encoding="utf-8")
                logging.info("daemon_ready marker was missing; restored")
            except OSError:
                pass

        time.sleep(0.02)  # 20ms poll interval


if __name__ == "__main__":
    raise SystemExit(serve())