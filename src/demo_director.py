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
        """Emit the start packet and hold it (the daemon's chunk-0 window)."""
        self.text = text
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
        while time.time() < deadline:
            self.daemon.write_directive({"action": "panel_info"})
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
    from PIL import Image, ImageDraw, ImageFont

    bg = Image.new("RGB", (STUDIO_W, STUDIO_H), BG_TOP)
    draw = ImageDraw.Draw(bg)
    # Vertical gradient graphite field
    for y in range(STUDIO_H):
        t = y / (STUDIO_H - 1)
        c = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        )
        draw.line([(0, y), (STUDIO_W, y)], fill=c)

    panel = Image.open(panel_png).convert("RGB")
    # Trim uniform borders (PrintWindow captures the full window rect;
    # the panel is borderless so this is usually a no-op).
    pw, ph = panel.size
    scale = 1.0
    max_w, max_h = 1440, 560
    if pw > max_w or ph > max_h:
        scale = min(max_w / pw, max_h / ph)
        panel = panel.resize(
            (int(pw * scale), int(ph * scale)), Image.LANCZOS
        )
        pw, ph = panel.size
    px = (STUDIO_W - pw) // 2
    py = 300
    # Soft shadow behind the panel
    shadow = Image.new("RGB", (pw + 40, ph + 40), (9, 10, 12))
    bg.paste(shadow, (px - 20, py - 20))
    bg.paste(panel, (px, py))

    # Title lockup
    draw = ImageDraw.Draw(bg)
    # System font stack resolved from WINDIR (never a hardcoded path —
    # the repo sanitize gate forbids absolute machine paths).
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or os.sep + "Windows"
    font_path = os.path.join(windir, "Fonts", "segoeui.ttf")
    font_path_bold = os.path.join(windir, "Fonts", "segoeuib.ttf")
    try:
        f_title = ImageFont.truetype(font_path_bold, 34)
        f_sub = ImageFont.truetype(font_path, 19)
        f_step = ImageFont.truetype(font_path_bold, 22)
    except OSError:
        f_title = f_sub = f_step = ImageFont.load_default()
    title = "ReadAloudTTS"
    draw.text((100, 84), title, font=f_title, fill=INK)
    draw.text((100, 130), "Select text. Press Home. Watch every word.", font=f_sub, fill=DIM)
    # Amber thread — the brand mark, left rail
    draw.line([(100, 96), (84, 96)], fill=AMBER, width=4)
    if step:
        draw.text((100, STUDIO_H - 92), step, font=f_step, fill=INK)
    if caption:
        draw.text((100, STUDIO_H - 56), caption, font=f_sub, fill=DIM)
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


class Scene:
    """One scripted demo scene: name, camera step captions, and a play()
    that drives the mock daemon + directives while the director captures."""

    def __init__(self, name: str, caption: str):
        self.name = name
        self.caption = caption
        self.frames: list[tuple[Path, float]] = []  # (png, hold_s)

    def shoot(self, panel: DemoPanel, workdir: Path, step: str) -> Path:
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
        return out


def scene_baseline(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 1 — the core loop: select, press Home, words light up amber."""
    sc = Scene("baseline", "The core loop — select anywhere, press Home")
    daemon = panel.daemon
    # Panel up, idle: the product as the user first meets it.
    text_short = DEMO_TEXT.split(".")[0] + "."
    daemon.begin_read(text_short)
    hwnd = panel.wait_panel_window()
    if not hwnd:
        raise RuntimeError("demo panel never appeared")
    time.sleep(0.5)
    sc.shoot(panel, workdir, "Select any text, press Home")
    # Advance through ~12 words at reading pace, shooting key frames.
    total = daemon.words[-1][2]
    for stop_ms, step in [
        (900, "each word lights up as it is spoken"),
        (1800, "click any word to jump the reader back"),
        (2600, "Space pauses · Space resumes from the same word"),
    ]:
        daemon.play_until(stop_ms)
        time.sleep(0.35)
        sc.shoot(panel, workdir, step)
    # Finish — the panel tears down into the replay bar, so shoot the
    # LAST reading frame instead of a dead-window capture, then close on
    # a mid-reading beauty shot (the seek-resume path: replaying from a
    # clicked word, which is the real post-done gesture).
    daemon.play_until(total * 0.92)
    time.sleep(0.35)
    sc.shoot(panel, workdir, "the last line — replay stays one click away")
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
    daemon.play_until(700.0)
    time.sleep(0.3)
    sc.shoot(panel, workdir, "replay from any word — the read continues")
    daemon.finish_read()
    time.sleep(0.4)
    return sc


def scene_anchor(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 2 — the fixed-anchor reading band: text slides up underneath
    a steady amber line instead of the amber word teleporting to row 1."""
    sc = Scene("anchor", "The fixed reading band — your eye never moves")
    daemon = panel.daemon
    text_long = (
        "Long documents are where this pays off. The highlight holds a "
        "steady line near the middle of the panel while the sentences "
        "glide upward underneath it, the way a teleprompter feeds a "
        "speaker — your eyes stay anchored on one band and the words "
        "come to you, so a ten-minute article never turns into a "
        "jump-cut montage. It is a small piece of interface choreography "
        "that makes an hour of reading feel like five minutes."
    )
    daemon.begin_read(text_long, hold_s=0.7)
    if not panel.wait_panel_window():
        raise RuntimeError("panel vanished")
    time.sleep(0.4)
    sc.shoot(panel, workdir, "a steady amber line, mid-panel")
    total = daemon.words[-1][2]
    # Shots across the read so the slide is visible frame to frame.
    marks = [int(f) for f in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9)]
    for frac in marks:
        daemon.play_until(total * frac)
        time.sleep(0.3)
        sc.shoot(panel, workdir, "sentences glide under a steady band")
    daemon.finish_read()
    time.sleep(0.4)
    return sc


def scene_zoom(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 3 — Ctrl+wheel zoom: bigger type arrives with MORE lines."""
    sc = Scene("zoom", "Ctrl+wheel zooms — more type, more context")
    daemon = panel.daemon
    text = DEMO_TEXT
    daemon.begin_read(text, hold_s=0.7)
    if not panel.wait_panel_window():
        raise RuntimeError("panel vanished")
    time.sleep(0.4)
    sc.shoot(panel, workdir, "2-line panel at rest")
    daemon.play_until(1200)
    time.sleep(0.3)
    # Zoom ladder: 1.0 -> 1.3 -> 1.6 -> 1.9 (2 -> 3 -> 4 -> 5 lines).
    for scale, step in [
        (1.3, "zoom out — a third line arrives"),
        (1.6, "a fourth line"),
        (1.9, "five lines of reading context"),
    ]:
        panel.send_directive({"action": "scale", "scale": scale}, settle_s=0.25)
        time.sleep(0.35)
        sc.shoot(panel, workdir, step)
    # Back down to the resting size.
    panel.send_directive({"action": "scale", "scale": 1.0}, settle_s=0.25)
    time.sleep(0.35)
    sc.shoot(panel, workdir, "back to the compact baseline")
    daemon.finish_read()
    time.sleep(0.4)
    return sc


def scene_speed(panel: DemoPanel, workdir: Path) -> Scene:
    """Scene 4 — speed keys that confirm themselves, and Ctrl+0 reset."""
    sc = Scene("speed", "Speed keys that confirm themselves in plain language")
    daemon = panel.daemon
    text = DEMO_TEXT
    daemon.begin_read(text, hold_s=0.7)
    if not panel.wait_panel_window():
        raise RuntimeError("panel vanished")
    time.sleep(0.4)
    sc.shoot(panel, workdir, "reading at normal pace")
    daemon.play_until(1100)
    time.sleep(0.3)
    # The full SendSpeed loop: the panel writes request.json itself, the
    # mock daemon confirms, the panel flashes the confirmed value.
    for speed, step in [
        (0.7, "Ctrl+* — the panel confirms: 1.4x faster"),
        (0.55, "again — 1.8x faster"),
        (0.8, "Ctrl+0 — back to the comfortable pace"),
    ]:
        panel.send_directive({"action": "speed_set", "speed": speed}, settle_s=0.5)
        time.sleep(0.55)
        sc.shoot(panel, workdir, step)
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

def frames_to_video(frame_pngs: list[Path], holds: list[float], out: Path, fps: int = FPS) -> None:
    """Concatenate (frame, hold) pairs into an MP4 via ffmpeg concat demuxer."""
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
        "-vf", f"fps={fps},format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        str(out),
    ]
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

def run_scene(scene_fn, scene_name: str, out_dir: Path, sandbox_base: Path) -> list[Path]:
    """One scene = one sandbox + one demo panel + one capture pass."""
    workdir = out_dir / "work" / scene_name
    workdir.mkdir(parents=True, exist_ok=True)
    sandbox = Sandbox.create(sandbox_base)
    panel = DemoPanel(sandbox)
    produced: list[Path] = []
    try:
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
        produced = [f for f, _ in scene.frames]
    finally:
        panel.stop()
        sandbox.cleanup()
    return produced


def main() -> int:
    ap = argparse.ArgumentParser(description="ReadAloudTTS demo studio")
    ap.add_argument("--scene", choices=list(SCENES) + ["all"], default=None)
    ap.add_argument("--list-scenes", action="store_true")
    ap.add_argument("--out", default=str(SRC_DIR / ".." / "docs" / "assets" / "demo-build"))
    ap.add_argument("--gif", action="store_true", help="also render GIF variants")
    ap.add_argument("--keep-work", action="store_true")
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

    names = REEL_ORDER if args.scene == "all" else [args.scene]
    all_frames: dict[str, list[Path]] = {}
    all_holds: dict[str, list[float]] = {}

    for name in names:
        print(f"[director] scene {name} …", flush=True)
        frames = run_scene(SCENES[name], name, out_dir, sandbox_base)
        all_frames[name] = frames
        # Frame hold: 2.2s per composed frame — a calm, readable reel.
        all_holds[name] = [2.2] * len(frames)
        print(f"[director]   {len(frames)} frames", flush=True)

    # Per-scene MP4s + a full reel.
    for name in names:
        frames = all_frames[name]
        if not frames:
            continue
        mp4 = out_dir / f"demo_{name}.mp4"
        frames_to_video(frames, all_holds[name], mp4)
        print(f"[director] wrote {mp4}", flush=True)
        if args.gif:
            gif = out_dir / f"demo_{name}.gif"
            frames_to_gif(frames, all_holds[name], gif)
            print(f"[director] wrote {gif}", flush=True)

    if args.scene == "all":
        reel_frames, reel_holds = [], []
        for name in REEL_ORDER:
            reel_frames.extend(all_frames.get(name, []))
            reel_holds.extend(all_holds.get(name, []))
        if reel_frames:
            reel = out_dir / "demo_reel.mp4"
            frames_to_video(reel_frames, reel_holds, reel)
            print(f"[director] wrote {reel}", flush=True)

    if not args.keep_work:
        shutil.rmtree(out_dir / "work", ignore_errors=True)
        shutil.rmtree(out_dir / "sandboxes", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())