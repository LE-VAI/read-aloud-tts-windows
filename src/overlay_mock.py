#!/usr/bin/env python3
"""overlay_mock.py — drive the overlay page end-to-end WITHOUT Piper.

Serves the real overlay.html + the real read-along component on
127.0.0.1:<port> via the real OverlayServer, then scripts a plausible
daemon state sequence into its injectable state source:

    start  -> playing ticks (~260ms/word, like the daemon's 30ms loop)
    -> seek re-start (suffix text + re-based timings, like handle_speak)
    -> more playing ticks
    -> done

A POST /seek from the page advances the script to the seek leg — the
same request the real daemon would receive, so the click-to-rewind
path is exercised for real.

Usage:  python src/overlay_mock.py [port]     (default 8793)
        python src/overlay_mock.py 8793 --duration 30
Kill with Ctrl+C (or close the window).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))

from overlay_server import OverlayServer  # noqa: E402

# The component lives as a sibling repo: <component source>
COMPONENT_ROOT = SRC_DIR.parents[0] / "read-along" / "src"
if not COMPONENT_ROOT.is_dir():
    COMPONENT_ROOT = SRC_DIR.parent.parent / "read-along" / "src"

# ~29 words — same shape the alignment proof used, incl. punctuation,
# an em-dash, quotes and a URL.
SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog. Piper speaks this — "
    'with an em-dash, "quotes", numbers like 42 and 3.14, and a URL '
    "https://example.com/path for good measure."
)

WORD_MS = 260.0  # reading pace; mirrors the demo card-5 host glue


def daemon_words_for(text: str) -> list[list]:
    """[[word, startMs, endMs], ...] — the daemon's _compute_word_timings
    shape (char-count distribution over a fixed duration)."""
    words = text.split()
    total_chars = sum(len(w) for w in words)
    duration = len(words) * WORD_MS
    out = []
    elapsed = 0.0
    for w in words:
        frac = len(w) / total_chars
        ms = duration * frac
        out.append([w, round(elapsed, 1), round(elapsed + ms, 1)])
        elapsed += ms
    return out


class ScriptedDaemon:
    """Injectable state source + request sink that mimics speak_server."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state: dict | None = None
        self.text: str | None = None
        self.requests: list[dict] = []
        self.stop_event = threading.Event()
        self.seek_incoming = threading.Event()   # main cycle yields to the leg
        self.leg_running = threading.Lock()      # one seek leg at a time

    # request sink: the overlay's POST /seek and /stop land here
    def __call__(self, request: dict) -> None:
        with self.lock:
            self.requests.append(request)
        print(f"[mock] request: {json.dumps(request)[:120]}", flush=True)
        if request.get("action") == "speak" and request.get("from_word", 0) > 0:
            # The real daemon: clear state, re-speak the suffix, re-emit
            # start with timings re-based to 0. Simulate synth latency.
            from_word = int(request["from_word"])
            if self.leg_running.acquire(blocking=False):
                threading.Thread(
                    target=self._run_seek_leg, args=(request["text"], from_word),
                    daemon=True,
                ).start()
            # a seek while a leg is already running is recorded but not
            # re-run — the mock serves one deterministic leg (the real
            # daemon chains speaks itself)

    def _run_seek_leg(self, text: str, from_word: int) -> None:
        try:
            words = text.split()
            if from_word >= len(words):
                return
            self.seek_incoming.set()  # main cycle stops ticking
            suffix = " ".join(words[from_word:])
            suffix_words = daemon_words_for(suffix)
            time.sleep(0.15)  # synth latency
            with self.lock:
                self.text = suffix
                self.state = {
                    "state": "start",
                    "text": suffix,
                    "words": suffix_words,
                    "total_ms": len(suffix_words) * WORD_MS,
                }
            # Hold the start state like the real daemon: its "start"
            # write lives from first-audio-ready until the first 30ms
            # playing tick — in practice ~1s (chunk-0 playback). A mock
            # that flips start->playing within 30ms is unobservable to
            # a 30ms poller (the page's poll + fetch round-trip exceeds
            # the window) and the page would never mount the seek text.
            time.sleep(0.8)
            # The leg's own ticks run even though seek_incoming is set
            # (it only stops the MAIN cycle).
            self._tick_until(len(suffix_words) * WORD_MS, ignore_seek=True)
            # The leg ends like the real daemon: a done state. A frozen
            # "playing" would leave the viewer page polling a stale ms
            # forever — the page's own end-detection needs the state
            # transition (and its 250ms host-silence fallback would
            # otherwise run the clock past the end on its own).
            with self.lock:
                self.state = {"state": "done"}
        finally:
            self.leg_running.release()

    # state source
    def read_state(self) -> dict:
        with self.lock:
            return dict(self.state) if self.state else {}

    def read_text(self) -> str | None:
        with self.lock:
            return self.text

    def _tick_until(self, total_ms: float, ignore_seek: bool = False) -> None:
        t0 = time.time()
        while not self.stop_event.is_set():
            if not ignore_seek and self.seek_incoming.is_set():
                return  # a seek arrived — the leg owns the state now
            elapsed = (time.time() - t0) * 1000.0
            if elapsed >= total_ms:
                return
            with self.lock:
                self.state = {"state": "playing", "ms": round(elapsed, 1)}
            time.sleep(0.03)  # the daemon's 30ms cadence

    def run(self, duration_s: float = 25.0) -> None:
        """Loop the scripted read until a seek arrives (or duration ends).

        Each cycle re-emits the full start->playing sequence, so a page
        that loads at ANY point (headless browsers are slow to spin up)
        always sees a fresh "start" within one cycle and joins. When a
        seek POST arrives, the cycle stops COMPLETELY (checked at every
        step, including the idle gap) — the seek leg owns the state from
        then on, exactly like the real daemon cancels old playback
        before speaking the suffix.
        """
        t_end = time.time() + duration_s
        while not self.stop_event.is_set() and time.time() < t_end:
            if self.seek_incoming.is_set():
                break
            time.sleep(0.4)  # small idle gap between cycles
            if self.seek_incoming.is_set():
                break
            words = daemon_words_for(SAMPLE_TEXT)
            total_ms = len(words) * WORD_MS
            with self.lock:
                if self.seek_incoming.is_set():
                    break
                self.text = SAMPLE_TEXT
                self.state = {
                    "state": "start",
                    "text": SAMPLE_TEXT,
                    "words": words,
                    "total_ms": total_ms,
                }
            # Hold the start state ~0.8s like the real daemon (see the
            # seek leg's note) so a 30ms poller always observes it.
            time.sleep(0.8)
            self._tick_until(total_ms)
            if self.seek_incoming.is_set():
                break  # the seek leg finishes the story
            with self.lock:
                if self.seek_incoming.is_set():
                    break
                self.state = {"state": "done"}
        # Idle after the script (or forever when a seek ended it) — the
        # server stays up for interactive poking.
        self.stop_event.wait(3600.0)


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8793
    duration = 25.0
    if "--duration" in sys.argv:
        duration = float(sys.argv[sys.argv.index("--duration") + 1])

    daemon = ScriptedDaemon()
    server = OverlayServer(
        state_source=daemon.read_state,
        text_source=daemon.read_text,
        request_sink=daemon,
        component_root=COMPONENT_ROOT,
        highlight_color="#FFC400",  # amber — the verification color
        host="127.0.0.1",
        port=port,
    )
    if not server.start():
        print(f"port {port} busy", file=sys.stderr)
        return 1
    print(f"overlay mock on http://127.0.0.1:{port}/overlay "
          f"(amber, component: {COMPONENT_ROOT})")
    print("script: fresh read ~7.5s, then idle; POST /seek triggers the "
          "seek leg; Ctrl+C to stop")
    try:
        daemon.run(duration)
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.stop_event.set()
        server.stop()
        print("\nrequests received:", json.dumps(daemon.requests, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())