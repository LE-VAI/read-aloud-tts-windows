#!/usr/bin/env python3
"""overlay_server.py — loopback HTTP viewer for the ReadAloudTTS web overlay.

Serves the karaoke reading overlay (src/overlay.html) and the read-along
web component files, bridges the daemon's highlight state to HTTP, and
forwards overlay clicks (seek / stop) into the daemon's request.json —
the exact file protocol the AutoHotkey UI already uses.

Routes (bound to 127.0.0.1 only):
  GET  /overlay             -> overlay.html with the configured
                               highlight_color injected as CSS vars
  GET  /highlight_state      -> current highlight state as JSON
                               ({"state":"idle"} when nothing exists);
                               bare "playing" states are merged with the
                               last text sidecar so a page opened mid-read
                               can still join
  GET  /component/<file>     -> whitelisted files from the component root
                               (ES modules must load same-origin)
  POST /seek                 -> request.json {"action":"speak",
                               "text":..., "from_word":N}
  POST /stop                 -> request.json {"action":"stop"}

The server owns no audio and never blocks the daemon: it is a viewer.
If the port is busy or the thread fails to start, the daemon logs it
and continues — the hotkeys still work.

Testable standalone: the state source and request sink are injectable,
so unit tests (and overlay_mock.py) drive this class without Piper.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

OVERLAY_HTML = Path(__file__).resolve().parent / "overlay.html"

# The component's import graph, exactly: overlay.html pulls in
# read-along.js, which imports tokenizer.js, highlight.js and
# engines/webspeech.js; overlay.html also imports engines/external.js.
# Anything else under the component root is refused.
COMPONENT_WHITELIST = {
    "read-along.js",
    "read-along.css",
    "tokenizer.js",
    "highlight.js",
    "engines/external.js",
    "engines/webspeech.js",
}

CONTENT_TYPES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}

# The component's own defaults (src/read-along.css) — injected when the
# config has no highlight_color so the page never carries raw markers.
DEFAULT_HIGHLIGHT = "rgb(64 132 255 / 0.5)"
DEFAULT_ACCENT = "rgb(33 74 255)"

MAX_POST_BYTES = 64 * 1024  # a seek carries the full text; cap matches max_chars headroom


def hex_to_rgb(color: str) -> tuple[int, int, int] | None:
    """Parse #RGB or #RRGGBB into (r, g, b); None when unparseable."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", color.strip())
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def highlight_vars(color: str | None) -> tuple[str, str]:
    """Map a configured hex color to (--ra-highlight, --ra-accent) values.

    Plain rgb() only — exotic color functions can silently drop inside
    ::highlight() paint rules (the component's documented trap).
    """
    if not color:
        return DEFAULT_HIGHLIGHT, DEFAULT_ACCENT
    rgb = hex_to_rgb(color)
    if rgb is None:
        return DEFAULT_HIGHLIGHT, DEFAULT_ACCENT
    r, g, b = rgb
    return f"rgb({r} {g} {b} / 0.55)", f"rgb({r} {g} {b} / 1)"


def render_overlay_html(color: str | None) -> bytes:
    """overlay.html with the %%HIGHLIGHT%% / %%ACCENT%% markers replaced."""
    highlight, accent = highlight_vars(color)
    html = OVERLAY_HTML.read_text(encoding="utf-8")
    if "%%HIGHLIGHT%%" not in html or "%%ACCENT%%" not in html:
        # The template must carry its markers; refuse to serve a page
        # whose styling silently fell back (that failure mode is the
        # invisible-highlight class of bug).
        raise ValueError("overlay.html is missing its color markers")
    html = html.replace("%%HIGHLIGHT%%", highlight).replace("%%ACCENT%%", accent)
    return html.encode("utf-8")


class OverlayServer:
    """Threadable loopback HTTP viewer around a state source + request sink.

    state_source: () -> dict          current highlight state ({} when none)
    text_source:   () -> str | None   last spoken text (the sidecar merge)
    request_sink:  (dict) -> None     receives {"action": ...} requests
    """

    def __init__(
        self,
        state_source: Callable[[], dict[str, Any]],
        request_sink: Callable[[dict[str, Any]], None],
        text_source: Callable[[], str | None] | None = None,
        component_root: Path | None = None,
        highlight_color: str | None = None,
        host: str = "127.0.0.1",
        port: int = 8792,
    ):
        self.state_source = state_source
        self.request_sink = request_sink
        self.text_source = text_source
        self.component_root = Path(component_root) if component_root else None
        self.highlight_color = highlight_color
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        # ?autotest=1 camera hold: GET /autotest-hold blocks until a
        # POST /autotest-hold (or the 30s cap) — the page's pending img
        # delays window.load so a headless screenshot waits for the
        # frozen receipt frame instead of shooting the placeholder.
        self._hold_released = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Bind and start serving on a daemon thread. False when the port
        is busy (the caller logs and carries on — hotkeys still work)."""
        try:
            self._httpd = ThreadingHTTPServer(
                (self.host, self.port), self._make_handler()
            )
        except OSError:
            return False
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="overlay-http", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    # -- request handling --------------------------------------------------

    def _current_state(self) -> dict[str, Any]:
        """The daemon's state, with bare playing states text-merged.

        The daemon's 30ms "playing" writes carry text only while the
        word list is still growing; the tail of a long read is bare
        {state, ms}. A page opened during that tail still needs the text
        to join mid-read, so the sidecar text (written once per speak)
        is merged in here — no extra per-30ms disk writes in the daemon.
        """
        try:
            state = dict(self.state_source() or {})
        except Exception:
            return {"state": "idle"}
        if not state.get("state"):
            # No state file (daemon between reads, or nothing ever spoken)
            # — report idle so the page shows its placeholder, never a
            # malformed state.
            return {"state": "idle"}
        if (
            state.get("state") == "playing"
            and "text" not in state
            and self.text_source is not None
        ):
            try:
                text = self.text_source()
            except Exception:
                text = None
            if text:
                state["text"] = text
        return state

    def _serve_component(self, rel: str) -> tuple[int, str, bytes]:
        if rel not in COMPONENT_WHITELIST or self.component_root is None:
            return 404, "text/plain; charset=utf-8", b"not found"
        # Defense in depth: resolve and verify the path stayed inside the
        # component root (the whitelist already blocks traversal, this
        # catches symlink surprises).
        path = (self.component_root / rel).resolve()
        try:
            path.relative_to(self.component_root.resolve())
        except ValueError:
            return 404, "text/plain; charset=utf-8", b"not found"
        if not path.is_file():
            return 404, "text/plain; charset=utf-8", b"not found"
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        return 200, ctype, path.read_bytes()

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # silence per-request stderr spam
                pass

            def _send(self, code: int, ctype: str, body: bytes) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/overlay":
                    try:
                        body = render_overlay_html(server.highlight_color)
                    except (OSError, ValueError):
                        self._send(500, "text/plain; charset=utf-8", b"overlay template error")
                        return
                    self._send(200, "text/html; charset=utf-8", body)
                elif path == "/highlight_state":
                    body = json.dumps(server._current_state()).encode("utf-8")
                    self._send(200, "application/json; charset=utf-8", body)
                elif path == "/autotest-hold":
                    # Long-poll up to 30s: the autotest page's <img> holds
                    # window.load (the headless camera's trigger) until the
                    # flow completes and it POSTs the release.
                    server._hold_released.wait(timeout=30.0)
                    self._send(204, "text/plain; charset=utf-8", b"")
                elif path.startswith("/component/"):
                    rel = path[len("/component/"):]
                    code, ctype, body = server._serve_component(rel)
                    self._send(code, ctype, body)
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found")

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > MAX_POST_BYTES:
                    self._send(413, "text/plain; charset=utf-8", b"payload too large")
                    return
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._send(400, "text/plain; charset=utf-8", b"bad json")
                    return
                if not isinstance(payload, dict):
                    self._send(400, "text/plain; charset=utf-8", b"bad payload")
                    return
                if path == "/seek":
                    text = payload.get("text")
                    from_word = payload.get("from_word", 0)
                    if not isinstance(text, str) or not text.strip():
                        self._send(400, "text/plain; charset=utf-8", b"text required")
                        return
                    try:
                        from_word = max(0, int(from_word))
                    except (TypeError, ValueError):
                        from_word = 0
                    server.request_sink(
                        {"action": "speak", "text": text, "from_word": from_word}
                    )
                    self._send(200, "application/json; charset=utf-8",
                               b'{"status":"ok","message":"seek submitted"}')
                elif path == "/stop":
                    server.request_sink({"action": "stop"})
                    self._send(200, "application/json; charset=utf-8",
                               b'{"status":"ok","message":"stop submitted"}')
                elif path == "/autotest-hold":
                    server._hold_released.set()
                    self._send(204, "text/plain; charset=utf-8", b"")
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found")

        return Handler


# -- daemon wiring helpers ---------------------------------------------------

def file_state_source(state_path: Path) -> Callable[[], dict[str, Any]]:
    """State source reading the daemon's highlight_state.json."""
    def read() -> dict[str, Any]:
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return read


def file_text_source(text_path: Path) -> Callable[[], str | None]:
    """Text source reading the daemon's once-per-speak sidecar."""
    def read() -> str | None:
        try:
            return json.loads(text_path.read_text(encoding="utf-8")).get("text")
        except (OSError, json.JSONDecodeError):
            return None
    return read


def file_request_sink(request_path: Path) -> Callable[[dict[str, Any]], None]:
    """Request sink writing the daemon's request.json atomically — the
    same file protocol AutoHotkey uses (the daemon polls and deletes it).
    Atomic replace so the daemon never reads a torn half-written request.
    """
    def write(request: dict[str, Any]) -> None:
        tmp = request_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(request), encoding="utf-8")
        tmp.replace(request_path)
    return write