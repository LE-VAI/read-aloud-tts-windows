"""Tests for overlay_server.py — the loopback HTTP overlay viewer.

Run:  python -m pytest src/test_overlay_server.py
Or:   python src/test_overlay_server.py

Exercises the viewer without Piper or audio: hex->rgb mapping, the
overlay page injection, whitelist enforcement, request forwarding into
the request-file protocol, and the mid-read text sidecar merge — over
real HTTP against a live in-process server instance.
"""

import json
import struct
import sys
import urllib.request
from pathlib import Path

# Make src/ importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from overlay_server import (
    COMPONENT_WHITELIST,
    DEFAULT_ACCENT,
    DEFAULT_HIGHLIGHT,
    OverlayServer,
    hex_to_rgb,
    highlight_vars,
    render_overlay_html,
    file_request_sink,
    file_state_source,
    file_text_source,
)


# ---------------------------------------------------------------------------
# hex_to_rgb / highlight_vars
# ---------------------------------------------------------------------------

def test_hex_to_rgb_full():
    assert hex_to_rgb("#FFC400") == (255, 196, 0)

def test_hex_to_rgb_short():
    assert hex_to_rgb("#fa0") == (255, 170, 0)

def test_hex_to_rgb_no_hash():
    assert hex_to_rgb("ffc400") == (255, 196, 0)

def test_hex_to_rgb_rejects_garbage():
    assert hex_to_rgb("not-a-color") is None
    assert hex_to_rgb("#12345") is None      # 5 digits: invalid
    assert hex_to_rgb("#1234567") is None    # 7 digits: invalid
    assert hex_to_rgb("") is None
    assert hex_to_rgb("rgb(1 2 3)") is None  # only hex forms accepted

def test_highlight_vars_none_uses_component_defaults():
    h, a = highlight_vars(None)
    assert h == DEFAULT_HIGHLIGHT
    assert a == DEFAULT_ACCENT

def test_highlight_vars_maps_amber():
    h, a = highlight_vars("#FFC400")
    assert h == "rgb(255 196 0 / 0.55)"
    assert a == "rgb(255 196 0 / 1)"

def test_highlight_vars_invalid_falls_back():
    h, a = highlight_vars("oops")
    assert h == DEFAULT_HIGHLIGHT
    assert a == DEFAULT_ACCENT

def test_highlight_vars_plain_rgb_only():
    # The documented trap: exotic color functions silently drop inside
    # ::highlight() paint rules. Both vars must stay plain rgb().
    for color in (None, "#00ff88", "junk"):
        h, a = highlight_vars(color)
        assert h.startswith("rgb(") and "color-mix" not in h
        assert a.startswith("rgb(") and "color-mix" not in a


# ---------------------------------------------------------------------------
# overlay template injection
# ---------------------------------------------------------------------------

def test_render_overlay_html_injects_color():
    body = render_overlay_html("#FFC400").decode("utf-8")
    assert "%%HIGHLIGHT%%" not in body
    assert "%%ACCENT%%" not in body
    assert "rgb(255 196 0 / 0.55)" in body

def test_render_overlay_html_default_keeps_markers_replaced():
    body = render_overlay_html(None).decode("utf-8")
    assert DEFAULT_HIGHLIGHT in body

def test_render_overlay_html_has_no_atlas_string():
    body = render_overlay_html("#FFC400").decode("utf-8")
    assert "the project" not in body  # public-facing naming rule


# ---------------------------------------------------------------------------
# live HTTP round-trips
# ---------------------------------------------------------------------------

# The read-along component lives as a SIBLING of this repo:
# <component source> (repo is <repo>).
COMPONENT_ROOT = Path(__file__).resolve().parents[2] / "read-along" / "src"


class _Harness:
    """A live OverlayServer on an ephemeral port with injectable state."""

    def __init__(self, tmp_path: Path):
        self.state = {}
        self.requests = []
        self.tmp = tmp_path
        self.server = OverlayServer(
            state_source=lambda: self.state,
            request_sink=self.requests.append,
            text_source=lambda: self._sidecar_text,
            component_root=COMPONENT_ROOT if COMPONENT_ROOT.is_dir() else None,
            highlight_color="#FFC400",
            host="127.0.0.1",
            port=0,  # ephemeral
        )
        self._sidecar_text = None
        assert self.server.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server._httpd.server_address[1]}"

    def get(self, path: str):
        req = urllib.request.Request(self.base + path)
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()

    def post(self, path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()

    def stop(self):
        self.server.stop()


def test_http_overlay_serves_injected_page(tmp_path):
    h = _Harness(tmp_path)
    try:
        status, body = h.get("/overlay")
        assert status == 200
        text = body.decode("utf-8")
        assert "rgb(255 196 0 / 0.55)" in text
        assert "%%" not in text
    finally:
        h.stop()

def test_http_state_idle_when_empty(tmp_path):
    h = _Harness(tmp_path)
    try:
        status, body = h.get("/highlight_state")
        assert status == 200
        assert json.loads(body) == {"state": "idle"}
    finally:
        h.stop()

def test_http_state_start_passes_through(tmp_path):
    h = _Harness(tmp_path)
    try:
        h.state = {"state": "start", "text": "hello world", "words": [["hello", 0.0, 100.0]]}
        status, body = h.get("/highlight_state")
        assert json.loads(body)["text"] == "hello world"
    finally:
        h.stop()

def test_http_state_bare_playing_merges_sidecar_text(tmp_path):
    # Mid-read join: tail-of-read playing states carry no text; the
    # sidecar must be merged in so a fresh page can still join.
    h = _Harness(tmp_path)
    try:
        h.state = {"state": "playing", "ms": 4321.0}
        h._sidecar_text = "the full reading text"
        status, body = h.get("/highlight_state")
        st = json.loads(body)
        assert st["text"] == "the full reading text"
        assert st["ms"] == 4321.0
    finally:
        h.stop()

def test_http_state_text_in_playing_not_overridden(tmp_path):
    h = _Harness(tmp_path)
    try:
        h.state = {"state": "playing", "ms": 10.0, "text": "growing-list text"}
        h._sidecar_text = "a different full text"
        status, body = h.get("/highlight_state")
        assert json.loads(body)["text"] == "growing-list text"
    finally:
        h.stop()

def test_http_seek_forwards_speak_request(tmp_path):
    h = _Harness(tmp_path)
    try:
        status, _ = h.post("/seek", {"text": "a b c d", "from_word": 2})
        assert status == 200
        assert h.requests == [
            {"action": "speak", "text": "a b c d", "from_word": 2}
        ]
    finally:
        h.stop()

def test_http_seek_requires_text(tmp_path):
    h = _Harness(tmp_path)
    try:
        try:
            h.post("/seek", {"from_word": 2})
            assert False, "expected 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
        assert h.requests == []
    finally:
        h.stop()

def test_http_seek_from_word_sanitized(tmp_path):
    h = _Harness(tmp_path)
    try:
        h.post("/seek", {"text": "w0 w1", "from_word": "-3"})
        h.post("/seek", {"text": "w0 w1", "from_word": "7"})
        assert h.requests[0]["from_word"] == 0
        assert h.requests[1]["from_word"] == 7
    finally:
        h.stop()

def test_http_stop_forwards_stop_request(tmp_path):
    h = _Harness(tmp_path)
    try:
        status, _ = h.post("/stop", {})
        assert status == 200
        assert h.requests == [{"action": "stop"}]
    finally:
        h.stop()

def test_http_component_whitelist(tmp_path):
    h = _Harness(tmp_path)
    try:
        if not COMPONENT_ROOT.is_dir():
            # component not present in this checkout — cover the refusal
            # path only (whitelist + traversal).
            try:
                h.get("/component/read-along.js")
                assert False, "expected 404"
            except urllib.error.HTTPError as e:
                assert e.code == 404
            return
        for rel in COMPONENT_WHITELIST:
            status, body = h.get("/component/" + rel)
            assert status == 200, rel
        for bad in ("../src/speak.py", "engines/kokoro.js", "whatever.js",
                    "read-along.css%00", "engines/../../speak.py"):
            try:
                status, _ = h.get("/component/" + bad)
            except urllib.error.HTTPError as e:
                status = e.code
            assert status == 404, bad
    finally:
        h.stop()

def test_http_unknown_route_404(tmp_path):
    h = _Harness(tmp_path)
    try:
        try:
            h.get("/nope")
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        h.stop()


# ---------------------------------------------------------------------------
# file-backed sources/sink (the daemon's wiring)
# ---------------------------------------------------------------------------

def test_file_state_source_reads_and_tolerates_missing(tmp_path):
    src = file_state_source(tmp_path / "state.json")
    assert src() == {}
    (tmp_path / "state.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
    assert src() == {"state": "done"}

def test_file_text_source_reads_and_tolerates_missing(tmp_path):
    src = file_text_source(tmp_path / "text.json")
    assert src() is None
    (tmp_path / "text.json").write_text(json.dumps({"text": "hi"}), encoding="utf-8")
    assert src() == "hi"

def test_file_request_sink_writes_atomic_protocol(tmp_path):
    sink = file_request_sink(tmp_path / "request.json")
    sink({"action": "stop"})
    assert json.loads((tmp_path / "request.json").read_text(encoding="utf-8")) == {
        "action": "stop"
    }
    assert not (tmp_path / "request.json.tmp").exists()


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                try:
                    fn(Path(td))
                    print(f"PASS {name}")
                except Exception as e:
                    failures += 1
                    print(f"FAIL {name}: {e}")
    print(f"\n{'ALL GREEN' if failures == 0 else str(failures) + ' FAILURES'}")
    sys.exit(1 if failures else 0)