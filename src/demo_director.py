#!/usr/bin/env python3
"""demo_director.py — the ReadAloudTTS self-serve demo studio.

Scripts the REAL product surfaces deterministically and renders branded
demo reels, with zero audio and zero live-daemon contact:

  Panel lane   — launches src/ReadAloudTTS.ahk with --demo-child inside an
                 isolated sandbox dir (copy of src + config), then plays
                 daemon: writes tmp\\highlight_state.json packets exactly
                 like speak_server's playback loop, answers request.json
                 with response.json, and drives pause/zoom/speed through
                 the child's directive file. Every frame is the real panel.
  Web lane     — serves the real overlay.html + read-along component via
                 OverlayServer (the overlay_mock.py pattern) and captures
                 a real browser window.

Captures are per-window PrintWindow (PW_RENDERFULLCONTENT — lock-safe,
occlusion-safe) composited onto the graphite studio backdrop, then
rendered to MP4/GIF via ffmpeg.

Usage:
    python src/demo_director.py --list-scenes
    python src/demo_director.py --scene baseline --out out_dir
    python src/demo_director.py --all --out out_dir
    python src/demo_director.py --all --gif --out out_dir

The director never touches the production daemon (port 8792), never
writes outside the sandbox + requested output dir, and never speaks.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import ctypes
from ctypes import wintypes
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent

# ffmpeg resolution: this machine sets a global FFMPEG env var pointing at
# the bin DIRECTORY (no exe name) — normalize both spellings, then fall
# back to PATH.
def _resolve_ffmpeg() -> str:
    raw = os.environ.get("FFMPEG", "").strip()
    if raw:
        p = Path(raw)
        if p.is_dir():
            exe = p / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            if exe.exists():
                return str(exe)
        if p.exists():
            return str(p)
    return "ffmpeg"  # PATH-resolved


FFMPEG = _resolve_ffmpeg()
# CreateProcess on the raw exe path can be blocked by the exec policy on
# this machine while shell-resolved invocation works; shell is the
# default. FFMPEG_SHELL=0 opts out.
_FFMPEG_SHELL = os.environ.get("FFMPEG_SHELL", "1") == "1"

AHK_EXE = os.environ.get(
    "AHK_EXE",
    os.path.expandvars(
        r"%LOCALAPPDATA%\Programs\AutoHotkey\v2\AutoHotkey64.exe"
    ),
)

# Studio look — the Reading Room backdrop: graphite field, amber accent.
# (Brand tokens mirror docs/assets renders; amber = the product highlight.)
BG_TOP = (22, 24, 29)      # #16181D
BG_BOTTOM = (13, 14, 18)   # #0D0E12
AMBER = (255, 196, 0)      # #FFC400
INK = (232, 233, 236)      # #E8E9EC
DIM = (122, 128, 139)      # #7A808B
PANEL_BG = (22, 22, 29)    # #16161D — the real panel background

STUDIO_W, STUDIO_H = 1600, 900
FPS = 30


# ---------------------------------------------------------------------------
# Win32 capture (PrintWindow, PW_RENDERFULLCONTENT)
# ---------------------------------------------------------------------------

PW_RENDERFULLCONTENT = 0x00000002
GA_ROOT = 2


def _root_hwnd(hwnd: int) -> int:
    user32 = ctypes.windll.user32
    return int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT))


def capture_window(hwnd: int, out_png: Path) -> bool:
    """PrintWindow a top-level window into out_png. Returns success."""
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # Window rect (screen coords)
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return False
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False

    hdc_window = user32.GetWindowDC(wintypes.HWND(hwnd))
    if not hdc_window:
        return False
    hdc_mem = gdi32.CreateCompatibleDC(hdc_window)
    bitmap = gdi32.CreateCompatibleBitmap(hdc_window, w, h)
    gdi32.SelectObject(hdc_mem, bitmap)
    # PW_RENDERFULLCONTENT (Win8.1+): renders even when the window is
    # occluded or the desktop is locked — the recipe the earlier GIF
    # demo run proved out.
    ok = bool(
        user32.PrintWindow(
            wintypes.HWND(hwnd), hdc_mem, PW_RENDERFULLCONTENT
        )
    )
    # BMP -> PNG via PIL so the frame dir is uniformly viewable.
    if ok:
        from PIL import Image  # local import — capture-only dependency

        class BMPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        class BITMAPFILEHEADER(ctypes.Structure):
            _pack_ = 2
            _fields_ = [
                ("bfType", wintypes.WORD),
                ("bfSize", wintypes.DWORD),
                ("bfReserved1", wintypes.WORD),
                ("bfReserved2", wintypes.WORD),
                ("bfOffBits", wintypes.DWORD),
            ]

        bmi = BMPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BMPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h  # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_mem, bitmap, 0, h, buf, ctypes.byref(bmi), 0)
        fh = BITMAPFILEHEADER()
        fh.bfType = 0x4D42
        fh.bfSize = ctypes.sizeof(BITMAPFILEHEADER) + w * h * 4
        fh.bfOffBits = ctypes.sizeof(BITMAPFILEHEADER)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        with open(out_png, "wb") as f:
            f.write(fh)
            f.write(bmi)  # bfOffBits == sizeof(fileheader) — pad ok
            f.write(buf.raw)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(wintypes.HWND(hwnd), hdc_window)
    return ok


def enum_windows() -> list[tuple[int, str, int, int, int, int]]:
    """[(hwnd, title, x, y, w, h)] for visible top-level windows."""
    user32 = ctypes.windll.user32
    results: list[tuple[int, str, int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        results.append(
            (
                int(hwnd),
                buf.value,
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
            )
        )
        return True

    user32.EnumWindows(cb, 0)
    return results


def find_demo_panel(panel_hwnd: int) -> int | None:
    """Root-window hwnd for the demo child's panel (its Gui is top-level)."""
    if panel_hwnd and ctypes.windll.user32.IsWindow(panel_hwnd):
        return _root_hwnd(panel_hwnd)
    return None


# ---------------------------------------------------------------------------
# Sandbox: an isolated copy of the repo's src where the demo child runs
# ---------------------------------------------------------------------------

class Sandbox:
    """Temp copy of src/ (the demo child's AppDir) with a stub config."""

    def __init__(self, root: Path):
        self.root = root
        self.tmp = root / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.config_path = root / "config.json"

    @classmethod
    def create(cls, base: Path) -> "Sandbox":
        base.mkdir(parents=True, exist_ok=True)
        root = base / "sandbox"
        if root.exists():
            shutil.rmtree(root)
        # Copy ONLY what a headless panel needs — not the whole src tree.
        # The AHK script references Msftedit (system), app.ico, and writes
        # into its own tmp/. speak.py/speak_server.py are NOT needed (no
        # daemon); overlay sources are NOT needed (panel lane only).
        root.mkdir(parents=True)
        src_files = ["ReadAloudTTS.ahk", "app.ico"]
        for name in src_files:
            s = SRC_DIR / name
            if s.exists():
                shutil.copy2(s, root / name)
        sandbox = cls(root)
        # Stub config: overlay on, 0.8 speed (the shipped factory default),
        # amber highlight. The child reads but never writes config in demo
        # mode (no set_speed persistence — the director confirms speeds by
        # dropping response.json itself).
        (root / "config.json").write_text(
            json.dumps(
                {
                    "highlight_overlay": True,
                    "length_scale": 0.8,
                    "highlight_color": "#FFC400",
                    "overlay_port": 8792,
                }
            ),
            encoding="utf-8",
        )
        return sandbox

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Offline narration (piper, the production voice) — audio for the MP4s.
# ---------------------------------------------------------------------------
# The demo child NEVER speaks (no daemon, no engine). Audio enters the reel
# after capture: each scene's narration is rendered offline to WAV through
# the production install's Piper (lessac voice + production prosody), one
# word per synth, concatenated with measured speech bounds and real pacing.
# The measured per-word durations then become the mock daemon's word
# timeline, so the amber highlight is synced to the audio you hear — the
# same measured-timing principle the karaoke panel itself uses.


class PiperNarrator:
    """Offline narration: per-word piper renders -> measured WAV segments.

    Fails closed: if the install venv or voice model is missing, enabled
    becomes False and the director renders silent video (exactly the v1
    behavior). Never touches the production daemon or live audio.
    """

    SAMPLE_RATE = 22050
    WORD_GAP_S = 0.06        # breath between words inside a sentence
    SENTENCE_PAUSE_S = 0.40  # the production sentence_silence feel

    def __init__(self) -> None:
        self.enabled = False
        self.reason = ""
        self._venv_python = self._find_venv_python()
        self._model = self._find_model()
        if not self._venv_python:
            self.reason = "install .venv python not found"
            return
        if not self._model:
            self.reason = "lessac voice model not found"
            return
        try:
            import wave  # noqa: F401 — stdlib wave present

            self.enabled = True
        except ImportError:
            self.reason = "wave module unavailable"

    @staticmethod
    def _find_venv_python() -> Path | None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        p = Path(local) / "ReadAloudTTS" / ".venv" / "Scripts" / "python.exe"
        return p if p.exists() else None

    @staticmethod
    def _find_model() -> Path | None:
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        p = Path(local) / "ReadAloudTTS" / "voices" / "en_US-lessac-medium.onnx"
        return p if p.exists() else None

    # -- piper invocation ----------------------------------------------------

    def _synthesize_word(self, word: str, out_wav: Path) -> None:
        """One piper call for one word — the production voice + prosody.

        length_scale 0.8 / noise_scale 0.4 / noise_w 0.3 are the shipped
        factory settings (config.example.json), so the demo voice is the
        voice users hear.
        """
        cmd = [
            str(self._venv_python), "-m", "piper",
            "--model", str(self._model),
            "--config", str(self._model) + ".json",
            "--output_file", str(out_wav),
            "--length-scale", "0.8",
            "--sentence-silence", "0.0",
            "--noise-scale", "0.4",
            "--noise-w", "0.3",
        ]
        subprocess.run(
            cmd, input=word, text=True, encoding="utf-8",
            capture_output=True, check=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @staticmethod
    def _speech_bounds(wav_path: Path) -> tuple[bytes, float]:
        """Return (trimmed_bytes, duration_s) — the speech-only span of a
        rendered word WAV, found by scanning 20ms RMS windows (manual
        unpack: audioop left the stdlib in 3.13)."""
        import wave

        with wave.open(str(wav_path), "rb") as w:
            fr = w.getframerate()
            n = w.getnframes()
            raw = w.readframes(n)
        win = max(int(fr * 0.02), 1)
        samp_w = 2  # 16-bit mono

        def rms(seg: bytes) -> float:
            if len(seg) < samp_w:
                return 0.0
            vals = struct.unpack(f"<{len(seg) // samp_w}h", seg)
            return (sum(v * v for v in vals) / max(len(vals), 1)) ** 0.5

        first = last = None
        for i in range(0, n - win, win):
            if rms(raw[i * samp_w:(i + win) * samp_w]) > 80.0:
                if first is None:
                    first = i
                last = i
        if first is None:
            return b"", 0.0
        seg = raw[first * samp_w:(last + win) * samp_w]
        return seg, len(seg) / samp_w / fr

    # -- scene narration ------------------------------------------------------

    def render_scene(
        self, text: str, workdir: Path
    ) -> tuple[Path | None, list[list], float]:
        """Render a scene's narration.

        Returns (wav_path, word_timeline_ms, sentence_pause_after_text_ms).
        word_timeline is per-word [word, start_ms, end_ms] measured from
        the actual audio — the mock daemon plays THIS timeline so the
        highlight moves with the voice. wav_path None => narration
        disabled; caller falls back to compute_words pacing.
        """
        if not self.enabled:
            return None, [], 0.0

        import wave

        workdir.mkdir(parents=True, exist_ok=True)
        segs: list[bytes] = []
        words: list[str] = text.split()
        timeline: list[list] = []
        elapsed = 0.0
        word_wav = workdir / "_word.wav"
        try:
            for i, word in enumerate(words):
                self._synthesize_word(word, word_wav)
                seg, dur = self._speech_bounds(word_wav)
                if dur <= 0.0:
                    dur = 0.18  # degenerate render — keep the timeline sane
                timeline.append([word, round(elapsed, 1), round(elapsed + dur * 1000.0, 1)])
                elapsed += dur * 1000.0
                segs.append(seg)
                # Sentence pause after sentence-ending punctuation.
                end_gap = (
                    self.SENTENCE_PAUSE_S
                    if word.rstrip('"\'")])}').endswith((".", "!", "?"))
                    else self.WORD_GAP_S
                )
                elapsed += end_gap * 1000.0
                segs.append(b"\x00\x00" * int(self.SAMPLE_RATE * end_gap))
        finally:
            word_wav.unlink(missing_ok=True)

        wav_path = workdir / "narration.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.SAMPLE_RATE)
            w.writeframes(b"".join(segs))
        # Clean per-word temp WAVs.
        for f in workdir.glob("pw_*.wav"):
            f.unlink(missing_ok=True)
        return wav_path, timeline, 0.0


# ---------------------------------------------------------------------------
# Mock daemon: writes the REAL wire files into the sandbox tmp dir
# ---------------------------------------------------------------------------

class MockDaemon:
    """Plays speak_server's file protocol against a --demo-child panel.

    State files (all inside sandbox tmp/):
      highlight_state.json — {"state":...} single-line JSON (the AHK
          tick polls this every 30ms)
      request.json         — the panel's own writes (set_speed/stop)
      response.json        — our replies, exactly like the real daemon
      panel.json           — the child's self-report (our eyes)
      demo_directives.json — one directive per write (pause/zoom/exit...)
    """

    def __init__(self, sandbox: Sandbox):
        self.dir = sandbox.tmp
        self.lock = threading.Lock()
        self.state: dict = {}
        self._stop = threading.Event()
        self.words: list[list] = []
        self.text = ""
        self.speed = 0.8  # matches the factory default
        self.directives_path = self.dir / "demo_directives.json"
        # Audio-sync mode: when the narrator measured a timeline for the
        # current scene, begin_read uses it (the highlight then matches
        # the muxed audio exactly). Falls back to char-weighted pacing.
        self.measured_timeline: list[list] | None = None
        self.narration_wav: Path | None = None

    # -- state file writers -------------------------------------------------

    def _write_state(self, state: dict) -> None:
        payload = json.dumps(state, separators=(",", ":")) + "\n"
        (self.dir / "highlight_state.json").write_text(payload, encoding="utf-8")

    def _write_response(self, payload: dict) -> None:
        (self.dir / "response.json").write_text(
            json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
        )

    def write_directive(self, directive: dict) -> None:
        self.directives_path.write_text(
            json.dumps(directive, separators=(",", ":")) + "\n", encoding="utf-8"
        )

    # -- word timing model ---------------------------------------------------

    def compute_words(self, text: str, wpm_ms: float = 300.0) -> list[list]:
        """Per-word [word, start_ms, end_ms] — char-count distribution,
        mirroring the daemon's _compute_word_timings shape."""
        words = text.split()
        total_chars = sum(len(w) for w in words)
        out = []
        elapsed = 0.0
        for w in words:
            ms = wpm_ms * (len(w) / total_chars) * len(words)
            out.append([w, round(elapsed, 1), round(elapsed + ms, 1)])
            elapsed += ms
        return out

    def begin_read(self, text: str, hold_s: float = 0.6) -> None:
        """Emit the start packet and hold it (the daemon's chunk-0 window).
        When a measured (audio-synced) timeline exists for this text,
        prefer it — the highlight then moves with the muxed narration."""
        self.text = text
        if (
            self.measured_timeline
            and self.narration_wav
            and text.split() == [w[0] for w in self.measured_timeline]
        ):
            self.words = self.measured_timeline
        else:
            self.words = self.compute_words(text)
        total_ms = self.words[-1][2] if self.words else 0.0
        with self.lock:
            self.state = {
                "state": "start",
                "text": text,
                "words": self.words,
                "total_ms": total_ms,
            }
            self._write_state(self.state)
        time.sleep(hold_s)

    def play_until(self, ms: float, tick_s: float = 0.03) -> None:
        """Emit bare playing packets at the daemon's 30ms cadence."""
        t0 = time.time()
        while (time.time() - t0) * 1000.0 < ms and not self._stop.is_set():
            with self.lock:
                self.state = {"state": "playing", "ms": round((time.time() - t0) * 1000.0, 1)}
                self._write_state(self.state)
            time.sleep(tick_s)

    def finish_read(self) -> None:
        with self.lock:
            self.state = {"state": "done"}
            self._write_state(self.state)

    # -- request pump (answers the panel's request.json writes) -------------

    def pump_requests(self) -> None:
        """Daemon-side request pump: watch request.json, respond, update
        internal speed. Runs in a thread for the whole capture."""
        req_path = self.dir / "request.json"
        resp_path = self.dir / "response.json"
        while not self._stop.is_set():
            try:
                if req_path.exists():
                    raw = req_path.read_text(encoding="utf-8").strip()
                    try:
                        req_path.unlink()
                    except OSError:
                        pass
                    if raw:
                        req = json.loads(raw)
                        action = req.get("action")
                        if action == "set_speed":
                            self.speed = float(req.get("speed", 1.0))
                            self._write_response(
                                {
                                    "status": "ok",
                                    "speed": self.speed,
                                    "message": f"Speed set to {self.speed}",
                                }
                            )
                        elif action == "stop":
                            self._write_response({"status": "ok", "message": "Stopped"})
                            with self.lock:
                                self.state = {"state": "stop"}
                                self._write_state(self.state)
                        elif action == "ping":
                            self._write_response({"status": "ok", "message": "pong"})
            except (json.JSONDecodeError, OSError, ValueError):
                pass
            time.sleep(0.02)

    # -- panel.json reader ----------------------------------------------------

    def read_panel(self, timeout_s: float = 5.0) -> dict | None:
        """Wait for a fresh panel.json (any age) and return its dict."""
        path = self.dir / "panel.json"
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            time.sleep(0.05)
        return None

    def wait_panel(
        self, predicate, timeout_s: float = 10.0, settle_s: float = 0.0
    ) -> dict | None:
        """Poll panel.json until predicate(p) is true (plus settle time)."""
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            p = self.read_panel(timeout_s=0.2)
            if p and predicate(p):
                if settle_s:
                    time.sleep(settle_s)
                    p = self.read_panel(timeout_s=1.0) or p
                return p
            time.sleep(0.05)
        return None

    def stop(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# Panel process (--demo-child)
# ---------------------------------------------------------------------------

class DemoPanel:
    """One --demo-child AHK process bound to one scene run."""

    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox
        self.proc: subprocess.Popen | None = None
        self.daemon = MockDaemon(sandbox)
        self.hwnd: int | None = None

    def start(self) -> None:
        script = self.sandbox.root / "ReadAloudTTS.ahk"
        # NOTE: no "*" argument — in AHK v2 a lone * means "read the script
        # from stdin", which silently no-ops under DEVNULL. The script path
        # is the first positional argument.
        cmd = [
            AHK_EXE,
            "/ErrorStdOut",
            str(script),
            "--demo-child",
            "--demo-parent",
            str(os.getpid()),
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(self.sandbox.root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        threading.Thread(target=self.daemon.pump_requests, daemon=True).start()

    def wait_panel_window(self, timeout_s: float = 15.0) -> int | None:
        """Wait until the child reports a live panel, return its root hwnd.

        Actively pokes panel_info directives AND demands a report written
        AFTER this call began (mtime-gated): the tick reporter is
        change-gated, so a stale report from the previous build (a dead
        hwnd after done→replay rebuild) would otherwise match forever.
        """
        deadline = time.time() + timeout_s
        t0 = time.time()
        panel_path = self.sandbox.tmp / "panel.json"
        try:
            m0 = panel_path.stat().st_mtime
        except OSError:
            m0 = 0.0
        seq = 0
        while time.time() < deadline:
            seq += 1
            # Nonce per poll: DemoDirectivePoll drops byte-identical
            # directives (static lastRaw gate). When the panel builds
            # BEFORE this wait begins — the normal case on a warm machine,
            # since begin_read's 0.6s hold outlives a cached AHK start —
            # the build-time report is already on disk (mt == m0) and no
            # playing packet flows until this call returns: an identical
            # poll never triggers a report and the loop deadlocks into
            # "demo panel never appeared". A nonce makes every poll
            # produce a fresh live report; the mtime gate still blocks
            # stale dead-hwnd reports from earlier builds (they are only
            # older, never newer, than m0).
            self.daemon.write_directive({"action": "panel_info", "seq": seq})
            time.sleep(0.15)
            p = self.daemon.read_panel(timeout_s=0.2)
            try:
                mt = panel_path.stat().st_mtime
            except OSError:
                mt = 0.0
            if p and p.get("gui") == 1 and p.get("hwnd") and mt > m0:
                self.hwnd = int(p["hwnd"])
                return self.hwnd
        return None

    def send_directive(self, directive: dict, settle_s: float = 0.12) -> None:
        self.daemon.write_directive(directive)
        time.sleep(settle_s)

    def stop(self) -> None:
        try:
            self.daemon.write_directive({"action": "exit"})
            if self.proc:
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Demo child is disposable and sandboxed; hard-exit via
                    # its own exit_now directive rather than taskkill.
                    self.daemon.write_directive({"action": "exit_now"})
                    try:
                        self.proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
        finally:
            self.daemon.stop()


# ---------------------------------------------------------------------------
# Studio compositor
# ---------------------------------------------------------------------------

def composite(panel_png: Path, out_png: Path, caption: str = "", step: str = "") -> None:
    """Place the captured panel on the graphite studio backdrop with the
    title lockup. The panel keeps its native pixels (crisp, no resample)."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    bg = Image.new("RGB", (STUDIO_W, STUDIO_H), BG_TOP)
    draw = ImageDraw.Draw(bg)
    # Vertical gradient graphite field
    for y in range(STUDIO_H):
        t = y / (STUDIO_H - 1)
        c = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        )
        draw.line([(0, y), (STUDIO_W, y)], fill=c)

    # Quiet depth: a soft amber-lit vignette behind the panel zone —
    # reads as studio lighting, stays under 2% luminance delta at edges.
    halo = Image.new("L", (STUDIO_W, STUDIO_H), 0)
    hdraw = ImageDraw.Draw(halo)
    hx, hy, hr = STUDIO_W // 2, 520, 560
    hdraw.ellipse([hx - hr, hy - int(hr * 0.62), hx + hr, hy + int(hr * 0.62)], fill=26)
    halo = halo.filter(ImageFilter.GaussianBlur(120))
    bg = Image.composite(
        Image.new("RGB", (STUDIO_W, STUDIO_H), (34, 36, 42)), bg, halo
    )
    draw = ImageDraw.Draw(bg)

    panel = Image.open(panel_png).convert("RGB")
    pw, ph = panel.size
    max_w, max_h = 1120, 480
    if pw > max_w or ph > max_h:
        scale = min(max_w / pw, max_h / ph)
        panel = panel.resize((int(pw * scale), int(ph * scale)), Image.LANCZOS)
        pw, ph = panel.size
    px = (STUDIO_W - pw) // 2
    py = 296
    # Layered shadow: a blurred soft cast + a tight contact edge — the
    # panel reads as physically lifted off the field, not pasted on.
    shadow_layer = Image.new("L", (STUDIO_W, STUDIO_H), 0)
    sdraw = ImageDraw.Draw(shadow_layer)
    sdraw.rounded_rectangle(
        [px - 14, py - 8, px + pw + 14, py + ph + 26], radius=18, fill=150
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(22))
    bg.paste(Image.new("RGB", (STUDIO_W, STUDIO_H), (5, 6, 8)), (0, 0), shadow_layer)
    bg.paste(panel, (px, py))

    # Title lockup — amber thread, wordmark, one-line promise.
    draw = ImageDraw.Draw(bg)
    # System font stack resolved from WINDIR (never a hardcoded path —
    # the repo sanitize gate forbids absolute machine paths).
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or os.sep + "Windows"
    font_path = os.path.join(windir, "Fonts", "segoeui.ttf")
    font_path_bold = os.path.join(windir, "Fonts", "segoeuib.ttf")
    font_path_semilight = os.path.join(windir, "Fonts", "segoeuil.ttf")
    try:
        f_title = ImageFont.truetype(font_path_bold, 44)
        f_sub = ImageFont.truetype(font_path_semilight, 21)
        f_step = ImageFont.truetype(font_path_bold, 24)
        f_caption = ImageFont.truetype(font_path, 20)
        f_kbd = ImageFont.truetype(font_path, 18)
    except OSError:
        f_title = f_sub = f_step = f_caption = f_kbd = ImageFont.load_default()
    # Amber thread: a short accent stroke that underlines the wordmark —
    # one confident mark, top-left, tying the frame to the product's
    # highlight color.
    draw.rounded_rectangle([96, 78, 128, 122], radius=5, fill=AMBER)
    draw.text((152, 78), "ReadAloudTTS", font=f_title, fill=INK)
    draw.text(
        (152, 136),
        "Every word lights up as it is read.",
        font=f_sub, fill=DIM,
    )

    # Step line: the caption sits in a quiet bottom band; the step gets
    # full ink weight so the eye reads THE POINT first, context second.
    if step:
        draw.line([(96, STUDIO_H - 118), (STUDIO_W - 96, STUDIO_H - 118)], fill=(38, 41, 48), width=1)
        draw.text((96, STUDIO_H - 94), step, font=f_step, fill=INK)
    if caption:
        draw.text((96, STUDIO_H - 54), caption, font=f_caption, fill=DIM)
    # Keycap hints, bottom-right — plain-text, product-true shortcuts.
    draw.text(
        (STUDIO_W - 96 - draw.textlength("Home  ·  Space  ·  Ctrl+wheel", font=f_kbd), STUDIO_H - 50),
        "Home  ·  Space  ·  Ctrl+wheel", font=f_kbd, fill=(90, 96, 106),
    )
    out_png.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out_png)


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

DEMO_TEXT = (
    "Select any text on your screen — an article, a PDF, a long email — "
    "and press Home. The words appear in a quiet panel and light up in "
    "amber as they are spoken. Click any word to jump the reader back to "
    "that line. Press Space to pause, and Space again to resume from the "
    "same word. Ctrl+wheel zooms the panel, and it grows new lines to "
    "keep your place in view. The speed keys confirm themselves in plain "
    "language, and Ctrl+zero brings the pace back to normal."
)

STEP_FONT = 300.0  # ms/word baseline pace in demo scenes

# Narration scripts — what the voice actually says, scene by scene. Written
# as short spoken lines (not screen text): the captions carry the UI story,
# the voice carries the product demo.
NARRATION = {
    "baseline": (
        "Select any text, and press Home. A quiet panel appears, and every "
        "word lights up as it is spoken. Click any word to jump back. "
        "Press space to pause, and space again to resume."
    ),
    "anchor": (
        "Long documents are where this pays off. The highlight holds a "
        "steady line, and the sentences glide upward underneath it, the "
        "way a teleprompter feeds a speaker. Your eyes never move."
    ),
    "zoom": (
        "Hold control and scroll, and the panel zooms. Bigger type arrives "
        "with more lines, not less. Your place in the text stays anchored."
    ),
    "speed": (
        "The speed keys confirm themselves in plain language. Faster, "
        "faster still, and control zero brings back the comfortable pace."
    ),
}


class Scene:
    """One scripted demo scene: name, camera step captions, and a play()
    that drives the mock daemon + directives while the director captures."""

    def __init__(self, name: str, caption: str):
        self.name = name
        self.caption = caption
        self.frames: list[tuple[Path, float]] = []  # (png, hold_s)
        self.narration: Path | None = None  # scene's WAV, if narration on

    def shot(self, panel: DemoPanel, workdir: Path, step: str) -> None:
        """Capture one named frame."""
        hwnd = panel.hwnd
        assert hwnd, "panel window not ready"
        idx = len(self.frames)
        raw = workdir / f"raw_{self.name}_{idx:04d}.png"
        if not capture_window(hwnd, raw):
            raise RuntimeError(f"PrintWindow failed for {self.name} frame {idx}")
        out = workdir / f"frame_{self.name}_{idx:04d}.png"
        composite(raw, out, caption=self.caption, step=step)
        self.frames.append((out, 0.0))
        try:
            raw.unlink()
        except OSError:
            pass


def scene_panel_text(panel: DemoPanel, scene_name: str, fallback: str) -> str:
    """When narration is on, the panel reads the NARRATION text — the
    measured timeline then makes the highlight move exactly with the
    muxed voice (what you hear is what lights up). Silent mode keeps
    the original demo text."""
    daemon = panel.daemon
    if daemon.narration_wav and daemon.measured_timeline:
        return NARRATION[scene_name]
    return fallback


def scene_baseline(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 1 — the core loop: select, press Home, words light up amber."""
    sc = Scene("baseline", "The core loop — select anywhere, press Home")
    daemon = panel.daemon
    # With narration on the panel reads the voice's script so the amber
    # highlight and the audio are word-locked; silent mode keeps the
    # original demo text.
    text_short = scene_panel_text(
        panel, "baseline", DEMO_TEXT.split(".")[0] + "."
    )
    daemon.begin_read(text_short)
    hwnd = panel.wait_panel_window()
    if not hwnd:
        raise RuntimeError("demo panel never appeared")
    time.sleep(0.5)
    sc.shot(panel, workdir, "Select any text, press Home")
    # Advance through the read at its actual pace (measured timeline when
    # narrated), shooting key frames at real fractions of the read.
    total = daemon.words[-1][2]
    for frac, step in [
        (0.18, "each word lights up as it is spoken"),
        (0.42, "click any word to jump the reader back"),
        (0.62, "Space pauses · Space resumes from the same word"),
    ]:
        daemon.play_until(total * frac)
        time.sleep(0.35)
        sc.shot(panel, workdir, step)
    # Finish — the panel tears down into the replay bar, so shoot the
    # LAST reading frame instead of a dead-window capture, then close on
    # a mid-reading beauty shot (the seek-resume path: replaying from a
    # clicked word, which is the real post-done gesture).
    daemon.play_until(total * 0.92)
    time.sleep(0.35)
    sc.shot(panel, workdir, "the last line — replay stays one click away")
    daemon.finish_read()
    time.sleep(0.6)
    # Seek-resume: the director re-emits the FULL text with a zero-timed
    # prefix (the daemon's exact seek packet) — the panel re-opens on the
    # same text at the resumed word, no rebuild flicker.
    resume_idx = max(1, len(daemon.words) // 3)
    resume_words = [[w, 0.0, 0.0] for w in daemon.words[:resume_idx]] + [
        [w, 0.0, 0.0] for w in daemon.words[resume_idx:]
    ]
    rebased = []
    elapsed = 0.0
    for w in daemon.words[resume_idx:]:
        span = w[2] - w[1]
        rebased.append([w[0], round(elapsed, 1), round(elapsed + span, 1)])
        elapsed += span
    packet_words = [
        [w[0], 0.0, 0.0] for w in daemon.words[:resume_idx]
    ] + rebased
    # The replay-bar click equivalent: SeekFromWord(0) restarts the
    # highlight timer (HighlightOnStop stopped it on done), arms
    # gSeekInFlight and DELETES the state file — so the seek-resume
    # packet must be written AFTER the replay directive, never before
    # (the child's delete would swallow it).
    panel.send_directive({"action": "replay"}, settle_s=0.25)
    daemon.text = text_short
    with daemon.lock:
        daemon.state = {
            "state": "start",
            "text": text_short,
            "words": packet_words,
            "total_ms": elapsed,
        }
        daemon._write_state(daemon.state)
    # The rebuild produces a NEW hwnd — re-resolve before shooting.
    if not panel.wait_panel_window():
        raise RuntimeError("panel did not rebuild for replay shot")
    time.sleep(0.6)
    daemon.play_until(elapsed * 0.35)
    time.sleep(0.3)
    sc.shot(panel, workdir, "replay from any word — the read continues")
    daemon.finish_read()
    time.sleep(0.4)
    return sc


def scene_anchor(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 2 — the fixed-anchor reading band: text slides up underneath
    a steady amber line instead of the amber word teleporting to row 1."""
    sc = Scene("anchor", "The fixed reading band — your eye never moves")
    daemon = panel.daemon
    text_long = scene_panel_text(panel, "anchor", (
        "Long documents are where this pays off. The highlight holds a "
        "steady line near the middle of the panel while the sentences "
        "glide upward underneath it, the way a teleprompter feeds a "
        "speaker — your eyes stay anchored on one band and the words "
        "come to you, so a ten-minute article never turns into a "
        "jump-cut montage. It is a small piece of interface choreography "
        "that makes an hour of reading feel like five minutes."
    ))
    daemon.begin_read(text_long, hold_s=0.7)
    if not panel.wait_panel_window():
        raise RuntimeError("panel vanished")
    time.sleep(0.4)
    sc.shot(panel, workdir, "a steady amber line, mid-panel")
    total = daemon.words[-1][2]
    # Shots across the read so the slide is visible frame to frame.
    marks = [int(f) for f in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    for frac in marks:
        daemon.play_until(total * frac)
        time.sleep(0.3)
        sc.shot(panel, workdir, "sentences glide under a steady band")
    daemon.finish_read()
    time.sleep(0.4)
    return sc


def scene_zoom(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 3 — Ctrl+wheel zoom: bigger type arrives with MORE lines."""
    sc = Scene("zoom", "Ctrl+wheel zooms — more type, more context")
    daemon = panel.daemon
    text = scene_panel_text(panel, "zoom", DEMO_TEXT)
    daemon.begin_read(text, hold_s=0.7)
    if not panel.wait_panel_window():
        raise RuntimeError("panel vanished")
    time.sleep(0.4)
    sc.shot(panel, workdir, "2-line panel at rest")
    total = daemon.words[-1][2]
    daemon.play_until(total * 0.18)
    time.sleep(0.3)
    # Zoom ladder: 1.0 -> 1.3 -> 1.6 -> 1.9 (2 -> 3 -> 4 -> 5 lines).
    for scale, frac, step in [
        (1.3, 0.38, "zoom out — a third line arrives"),
        (1.6, 0.52, "a fourth line"),
        (1.9, 0.66, "five lines of reading context"),
    ]:
        panel.send_directive({"action": "scale", "scale": scale}, settle_s=0.25)
        daemon.play_until(total * frac)
        time.sleep(0.35)
        sc.shot(panel, workdir, step)
    # Back down to the resting size.
    panel.send_directive({"action": "scale", "scale": 1.0}, settle_s=0.25)
    daemon.play_until(total * 0.8)
    time.sleep(0.35)
    sc.shot(panel, workdir, "back to the compact baseline")
    daemon.finish_read()
    time.sleep(0.4)
    return sc


def scene_speed(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 4 — speed keys that confirm themselves, and Ctrl+0 reset."""
    sc = Scene("speed", "Speed keys that confirm themselves in plain language")
    daemon = panel.daemon
    text = scene_panel_text(panel, "speed", DEMO_TEXT)
    daemon.begin_read(text, hold_s=0.7)
    if not panel.wait_panel_window():
        raise RuntimeError("panel vanished")
    time.sleep(0.4)
    sc.shot(panel, workdir, "reading at normal pace")
    total = daemon.words[-1][2]
    daemon.play_until(total * 0.2)
    time.sleep(0.3)
    # The full SendSpeed loop: the panel writes request.json itself, the
    # mock daemon confirms, the panel flashes the confirmed value.
    # Factors, not absolute speeds: speed_set rides the REAL SendSpeed
    # path, which multiplies by the sandbox config's length_scale (0.8,
    # frozen in demo mode — the child never rewrites config). 0.8*0.875
    # confirms 0.7 ("1.4x faster"), 0.8*0.6875 confirms 0.55 ("1.8x
    # faster"), and Ctrl+0 as a 1.0 factor hits SendSpeed's no-change
    # branch — the genuine reset flash at the 0.8 comfortable pace.
    # Shot BEFORE play_until: the amber confirmation flash lives 1.5s
    # (0.9s for the no-change branch), so the camera must fire inside
    # that window — advancing the read first consumed it and every
    # frame caught the restored hint line instead.
    for factor, frac, step in [
        (0.875, 0.42, "Ctrl+* — the panel confirms: 1.4x faster"),
        (0.6875, 0.52, "again — 1.8x faster"),
        (1.0, 0.72, "Ctrl+0 — back to the comfortable pace"),
    ]:
        panel.send_directive({"action": "speed_set", "speed": factor}, settle_s=0.5)
        sc.shot(panel, workdir, step)
        daemon.play_until(total * frac)
        time.sleep(0.25)
    daemon.finish_read()
    time.sleep(0.4)
    return sc


SCENES = {
    "baseline": scene_baseline,
    "anchor": scene_anchor,
    "zoom": scene_zoom,
    "speed": scene_speed,
}

# Reel assembly order (baseline first — the hook).
REEL_ORDER = ["baseline", "anchor", "zoom", "speed"]


# ---------------------------------------------------------------------------
# Render (ffmpeg)
# ---------------------------------------------------------------------------

def _wave_open(path: Path):
    import wave

    return wave.open(str(path), "rb")


def frames_to_video(
    frame_pngs: list[Path],
    holds: list[float],
    out: Path,
    fps: int = FPS,
    narration: Path | None = None,
) -> None:
    """Concatenate (frame, hold) pairs into an MP4 via ffmpeg concat demuxer.
    narration (WAV) is muxed as AAC when present; video duration wins and
    the audio is padded/truncated to match."""
    concat = out.parent / f"{out.stem}_concat.txt"
    lines = []
    for png, hold in zip(frame_pngs, holds):
        dur = max(hold, 1.0 / fps)
        # ffmpeg concat demuxer wants forward slashes / escaped colons.
        p = str(png.resolve()).replace("\\", "/")
        lines.append(f"file '{p}'")
        lines.append(f"duration {dur:.3f}")
    # concat demuxer quirk: repeat the last file so its duration applies.
    lines.append(f"file '{str(frame_pngs[-1].resolve()).replace(chr(92), '/')}'")
    concat.write_text("\n".join(lines), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
    ]
    if narration and narration.exists():
        # Audio enters here, post-capture: the demo child never spoke.
        cmd += ["-i", str(narration)]
    cmd += [
        "-vf", f"fps={fps},format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
    ]
    if narration and narration.exists():
        # AAC 128k mono; -shortest trims tail, apad keeps audio alive if
        # the video runs long.
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "1", "-af", "apad", "-shortest"]
    cmd += [str(out)]
    _run_ffmpeg(cmd)
    concat.unlink(missing_ok=True)


def _run_ffmpeg(cmd: list[str]) -> None:
    """Run an ffmpeg argv, shell-resolving when the raw CreateProcess is
    blocked by the exec policy (Windows sandbox: direct exe launch denied,
    PATH shell launch fine)."""
    if _FFMPEG_SHELL:
        subprocess.run(
            subprocess.list2cmdline(cmd), shell=True, check=True,
            capture_output=True,
        )
    else:
        subprocess.run(cmd, check=True, capture_output=True)


def frames_to_gif(frame_pngs: list[Path], holds: list[float], out: Path) -> None:
    """GIF via palette generation (the quality path)."""
    concat = out.parent / f"{out.stem}_concat.txt"
    lines = []
    for png, hold in zip(frame_pngs, holds):
        lines.append(f"file '{str(png.resolve()).replace(chr(92), '/')}'")
        lines.append(f"duration {max(hold, 0.05):.3f}")
    lines.append(f"file '{str(frame_pngs[-1].resolve()).replace(chr(92), '/')}'")
    concat.write_text("\n".join(lines), encoding="utf-8")
    palette = out.parent / f"{out.stem}_palette.png"
    _run_ffmpeg(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-vf", "fps=12,scale=960:-1:flags=lanczos,palettegen", str(palette)]
    )
    _run_ffmpeg(
        [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-i", str(palette),
         "-lavfi", "fps=12,scale=960:-1:flags=lanczos[x];[x][1:v]paletteuse",
         str(out)]
    )
    concat.unlink(missing_ok=True)
    palette.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_scene(scene_fn, scene_name: str, out_dir: Path, sandbox_base: Path,
              narrator: PiperNarrator | None) -> list[Path]:
    """One scene = one sandbox + one demo panel + one capture pass."""
    workdir = out_dir / "work" / scene_name
    workdir.mkdir(parents=True, exist_ok=True)
    sandbox = Sandbox.create(sandbox_base)
    panel = DemoPanel(sandbox)
    produced: list[Path] = []
    narration: Path | None = None
    try:
        # Offline narration FIRST — its measured word timeline drives the
        # mock daemon, so the amber highlight moves with the actual voice.
        if narrator and narrator.enabled:
            script = NARRATION.get(scene_name)
            if script:
                wav, timeline, _ = narrator.render_scene(script, workdir)
                if wav and timeline:
                    panel.daemon.measured_timeline = timeline
                    panel.daemon.narration_wav = wav
                    narration = wav
                    print(f"[director]   narration {len(timeline)} words, "
                          f"{timeline[-1][2] / 1000.0:.1f}s", flush=True)
        panel.start()
        try:
            scene = scene_fn(panel, workdir)
        except Exception:
            # Child diagnostics for a failed scene: the AHK DebugLog.
            log = sandbox.tmp / "working_debug.log"
            if log.exists():
                print(f"--- {scene_name} child log tail ---", file=sys.stderr)
                print("\n".join(log.read_text(encoding="utf-8").splitlines()[-25:]), file=sys.stderr)
            raise
        scene.narration = narration
        produced = [f for f, _ in scene.frames]
    finally:
        panel.stop()
        sandbox.cleanup()
    return produced, narration


def main() -> int:
    ap = argparse.ArgumentParser(description="ReadAloudTTS demo studio")
    ap.add_argument("--scene", choices=list(SCENES) + ["all"], default=None)
    ap.add_argument("--list-scenes", action="store_true")
    ap.add_argument("--out", default=str(SRC_DIR / ".." / "docs" / "assets" / "demo-build"))
    ap.add_argument("--gif", action="store_true", help="also render GIF variants")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument(
        "--no-audio", action="store_true",
        help="render silent MP4s (skip the offline piper narration pass)",
    )
    args = ap.parse_args()

    if args.list_scenes:
        for name in REEL_ORDER:
            print(name)
        return 0

    if not args.scene:
        print("nothing to do — pass --scene <name|all> (see --list-scenes)", file=sys.stderr)
        return 1

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sandbox_base = out_dir / "sandboxes"
    sandbox_base.mkdir(parents=True, exist_ok=True)

    narrator = None
    if not args.no_audio:
        narrator = PiperNarrator()
        if narrator.enabled:
            print("[director] narration: piper (production voice, offline)", flush=True)
        else:
            print(f"[director] narration disabled: {narrator.reason}", flush=True)

    names = REEL_ORDER if args.scene == "all" else [args.scene]
    all_frames: dict[str, list[Path]] = {}
    all_holds: dict[str, list[float]] = {}
    all_narration: dict[str, Path | None] = {}

    for name in names:
        print(f"[director] scene {name} …", flush=True)
        frames, narration = run_scene(SCENES[name], name, out_dir, sandbox_base, narrator)
        all_frames[name] = frames
        all_narration[name] = narration
        if narration:
            # Voiced scene: the voice paces the scene — spread the frames
            # across the narration span so the read finishes with the audio
            # (no -shortest truncation mid-word, no silent tail).
            with _wave_open(narration) as w:
                audio_s = w.getnframes() / w.getframerate()
            hold = max(audio_s / len(frames), 1.0 / FPS)
            all_holds[name] = [hold] * len(frames)
            print(f"[director]   {len(frames)} frames · {audio_s:.1f}s narration "
                  f"· {hold:.2f}s hold", flush=True)
        else:
            # Frame hold: 2.2s per composed frame — a calm, readable reel.
            all_holds[name] = [2.2] * len(frames)
            print(f"[director]   {len(frames)} frames", flush=True)

    # Per-scene MP4s + a full reel.
    for name in names:
        frames = all_frames[name]
        if not frames:
            continue
        mp4 = out_dir / f"demo_{name}.mp4"
        frames_to_video(frames, all_holds[name], mp4, narration=all_narration.get(name))
        print(f"[director] wrote {mp4}", flush=True)
        if args.gif:
            gif = out_dir / f"demo_{name}.gif"
            frames_to_gif(frames, all_holds[name], gif)
            print(f"[director] wrote {gif}", flush=True)

    if args.scene == "all":
        reel_frames, reel_holds = [], []
        reel_audios = []
        for name in REEL_ORDER:
            reel_frames.extend(all_frames.get(name, []))
            reel_holds.extend(all_holds.get(name, []))
        if reel_frames:
            reel = out_dir / "demo_reel.mp4"
            # The reel muxes only when EVERY scene carries narration — a
            # half-voiced reel reads as a defect. Partial audio => silent
            # reel (still fully usable; per-scene MP4s carry the voice).
            first_audio = next((all_narration[n] for n in REEL_ORDER
                                if all_narration.get(n)), None)
            full_audio = first_audio and all(all_narration.get(n) for n in REEL_ORDER)
            frames_to_video(
                reel_frames, reel_holds, reel,
                narration=first_audio if full_audio else None,
            )
            print(f"[director] wrote {reel}", flush=True)

    if not args.keep_work:
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        shutil.rmtree(out_dir / "sandboxes", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())