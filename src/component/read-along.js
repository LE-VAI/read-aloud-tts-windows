/**
 * read-along.js — <read-along> custom element: bimodal read-along player.
 *
 * Usage:
 *   <read-along>
 *     <p>Any inline markup. The words light up as they're spoken.</p>
 *   </read-along>
 *
 * The element wraps host content in a player (play/pause, restart, speed) and
 * speaks the text with a pluggable engine. Default engine: Web Speech with
 * the Chrome ~15s cutoff defeated. Pass your own engine via the `engine`
 * property — anything implementing { speak(chunks), pause(), resume(),
 * stop(), setChunks() } and calling back onToken/onChunkStart/onEnd works
 * (e.g. a kokoro-js or Piper WASM adapter, or a pre-synthesized-audio
 * engine with per-chunk audio + timings).
 *
 * Engine-agnostic by design: the controller never touches speechSynthesis;
 * it only consumes token/chunk offsets and drives the Highlighter.
 */

import { tokenize, chunkTokens } from "./tokenizer.js";
import {
  Highlighter,
  buildTokenRanges,
  supportsHighlightAPI,
} from "./highlight.js";
import { WebSpeechEngine } from "./engines/webspeech.js";

const template = document.createElement("template");
template.innerHTML = `
  <style>
    :host { display: block; }
    .ra-wrap { display: grid; gap: 10px; }
    .ra-controls {
      display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
      font: 500 13px/1.2 ui-sans-serif, system-ui, sans-serif;
      color: CanvasText;
    }
    .ra-btn {
      display: inline-flex; align-items: center; justify-content: center;
      gap: 6px; min-height: 30px; min-width: 30px; padding: 5px 12px;
      border-radius: 999px; border: 1px solid color-mix(in oklab, currentColor 30%, transparent);
      background: Canvas; color: CanvasText; cursor: pointer;
    }
    .ra-btn:hover { border-color: currentColor; }
    .ra-btn:focus-visible, .ra-speed:focus-visible {
      outline: 2px solid Highlight; outline-offset: 2px;
    }
    .ra-btn[aria-pressed="true"] { background: color-mix(in oklab, Highlight 18%, Canvas); }
    .ra-btn svg { width: 13px; height: 13px; fill: currentColor; flex: none; }
    .ra-status { font-variant-numeric: tabular-nums; opacity: 0.75; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ra-speed { border-radius: 8px; padding: 5px 7px; border: 1px solid color-mix(in oklab, currentColor 25%, transparent); background: Canvas; color: CanvasText; }
    .ra-content { display: block; }
  </style>
  <div class="ra-wrap" part="wrap">
    <div class="ra-controls" role="group" aria-label="Read-along controls">
      <button class="ra-btn" id="play" aria-pressed="false">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path id="playicon" d="M4 2.5v11l9-5.5z"/></svg>
        <span id="playlabel">Listen</span>
      </button>
      <button class="ra-btn" id="restart" aria-label="Restart from the beginning">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3a5 5 0 1 1-4.9 6h1.55A3.5 3.5 0 1 0 8 4.5V7L4 4l4-3z"/></svg>
      </button>
      <label class="ra-nowrap" style="display:inline-flex;align-items:center;gap:6px">
        <span class="ra-status" id="status">Ready</span>
      </label>
      <select class="ra-speed" id="speed" aria-label="Reading speed">
        <option value="0.75">0.75×</option>
        <option value="1" selected>1×</option>
        <option value="1.25">1.25×</option>
        <option value="1.5">1.5×</option>
        <option value="2">2×</option>
      </select>
    </div>
    <div class="ra-content"><slot></slot></div>
    <p id="ra-live" aria-live="polite" style="position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip-path:inset(50%);"></p>
  </div>
`;

const ICON_PLAY = "M4 2.5v11l9-5.5z";
const ICON_PAUSE = "M3.5 2.5h3.2v11H3.5zM9.3 2.5h3.2v11H9.3z";

/** Instances holding the voice right now — enforces a one-voice policy. */
const ACTIVE = new Set();

class ReadAlong extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.appendChild(template.content.cloneNode(true));
    this._engine = null;
    this._tokens = [];
    this._chunks = [];
    this._highlighter = null;
    this._state = "idle"; // idle | playing | paused
    this._wireUI();
  }

  connectedCallback() {
    if (this._prepared && this._highlighter?.destroyed) {
      // Re-connected after disconnect destroyed the highlighter — rebuild
      // it and re-map token ranges (the DOM may have changed too).
      this._highlighter = new Highlighter(this, {
        forceFallback: this.hasAttribute("force-fallback"),
      });
      this._highlighter.setTokenRanges(buildTokenRanges(this, this._tokens));
    }
    this._prepare();
  }

  disconnectedCallback() {
    this.stop();
    this._highlighter?.destroy();
  }

  // -- public API ----------------------------------------------------------

  get state() { return this._state; }

  /** Engine instance (WebSpeechEngine default). Set before first play. */
  get engine() { return this._engine; }
  set engine(e) {
    if (this._state !== "idle") this.stop();
    this._engine = e;
    this._bindEngine();
    if (e && this._chunks.length) e.setChunks?.(this._chunks);
  }

  play() { this._play(); }
  pause() { this._pause(); }
  toggle() { this._state === "playing" ? this._pause() : this._play(); }
  stop() { this._stop(); }

  /**
   * Seek to a token index and start playing from there (word granularity).
   * No-op while paused — resume first, or stop() then seekToToken().
   * @param {number} i token index (0-based)
   */
  seekToToken(i) {
    this._prepare();
    if (!this._engine || this._state === "paused") return;
    if (i < 0 || i >= this._tokens.length) return;
    this._stop();
    this._state = "playing";
    this._bindEngine();
    this._engine.rate = parseFloat(this._els.speed.value) || 1;
    this._engine.speak(this._chunks, i);
    this._setPlayingUi(true);
    this._announce(`Playing from word ${i + 1}`);
    this._emitEvent("seek");
  }

  /** Index of the word currently being spoken (-1 when idle). */
  get activeToken() { return this._highlighter ? this._highlighter._markIndex : -1; }

  static get observedAttributes() { return ["lang", "rate", "seekable"]; }

  attributeChangedCallback(name, _old, value) {
    if (!this._engine) return;
    if (name === "lang") this._engine.lang = value;
    if (name === "rate") this._engine.rate = parseFloat(value) || 1;
  }

  // -- internals -----------------------------------------------------------

  _wireUI() {
    const $ = (id) => this.shadowRoot.getElementById(id);
    this._els = {
      play: $("play"), playicon: $("playicon"), playlabel: $("playlabel"),
      restart: $("restart"), status: $("status"), speed: $("speed"),
      live: $("ra-live"),
    };
    this._els.play.addEventListener("click", () => this.toggle());
    this._els.restart.addEventListener("click", () => {
      this.stop();
      this.play();
    });
    this._els.speed.addEventListener("change", () => {
      const r = parseFloat(this._els.speed.value) || 1;
      if (this._engine) this._engine.rate = r;
    });
  }

  _prepare() {
    if (this._prepared) return;
    this._prepared = true;
    // Tokenize the RAW textContent — no whitespace collapsing, because
    // offsets must match what the TreeWalker sees in the real text nodes.
    const text = this.textContent || "";
    this._tokens = tokenize(text);
    this._chunks = chunkTokens(this._tokens);
    this._highlighter = new Highlighter(this, {
      forceFallback: this.hasAttribute("force-fallback"),
    });
    this._highlighter.setTokenRanges(buildTokenRanges(this, this._tokens));
    if (this.hasAttribute("seekable")) this._wireSeek();
    if (!this._engine) {
      this.engine = new WebSpeechEngine({
        lang: this.getAttribute("lang") || undefined,
        rate: parseFloat(this.getAttribute("rate")) || 1,
      });
    }
  }

  // -- click-to-seek ---------------------------------------------------------

  /**
   * When the `seekable` attribute is present, clicks/taps on the host's
   * words restart playback from that word. Word hit-testing reuses the
   * token ranges (a click inside a token's Range owns that token); clicks
   * between words fall to the NEAREST token, so every tap seeks somewhere
   * useful instead of only exact hits.
   */
  _wireSeek() {
    if (this._seekWired) return;
    this._seekWired = true;
    const isInteractive = (el) =>
      el.closest && el.closest("a, button, input, select, textarea, [contenteditable]");
    const handler = (ev) => {
      if (this._state !== "playing") return; // seek only while reading
      if (isInteractive(ev.target)) return;  // never steal link/button clicks
      const i = this._tokenAt(ev);
      if (i >= 0) {
        ev.preventDefault();
        this.seekToToken(i);
      }
    };
    this.addEventListener("pointerdown", handler);
  }

  /** Token index under a pointer event, or nearest if between words. */
  _tokenAt(ev) {
    const host = this;
    if (document.caretRangeFromPoint) {
      const r = document.caretRangeFromPoint(ev.clientX, ev.clientY);
      if (r && host.contains(r.startContainer)) return this._tokenForOffset(r.startContainer, r.startOffset, true);
    } else if (document.caretPositionFromPoint) {
      const p = document.caretPositionFromPoint(ev.clientX, ev.clientY);
      if (p && host.contains(p.offsetNode)) return this._tokenForOffset(p.offsetNode, p.offset, true);
    }
    return -1;
  }

  /**
   * Map a (text node, offset) pair to a token index by binary search over
   * the concatenated text. When the offset falls in whitespace (not inside
   * any token), snap to the nearest token.
   */
  _tokenForOffset(node, offset, _snap) {
    // Absolute offset of this node's start within the host's text stream.
    const walker = document.createTreeWalker(this, NodeFilter.SHOW_TEXT);
    let absBase = 0, n = walker.nextNode(), target = null, targetBase = 0;
    while (n !== null) {
      if (n === node) { target = n; targetBase = absBase; break; }
      absBase += n.data.length;
      n = walker.nextNode();
    }
    if (!target) return -1;
    const abs = targetBase + offset;
    // Binary search tokens by [start, end).
    let lo = 0, hi = this._tokens.length - 1, best = -1, bestDist = Infinity;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const t = this._tokens[mid];
      if (abs < t.start) { hi = mid - 1; }
      else if (abs >= t.end) { lo = mid + 1; }
      else return t.index; // inside a word
      // Track nearest edge for the whitespace-snap fallback.
      const d = Math.min(Math.abs(abs - t.start), Math.abs(abs - t.end));
      if (d < bestDist) { bestDist = d; best = t.index; }
    }
    return best; // between words → nearest
  }

  _bindEngine() {
    const e = this._engine;
    if (!e) return;
    e.onToken = (i) => {
      this._highlighter?.setActive(i);
    };
    e.onChunkStart = (idx, chunk) => {
      // Sentence tint under the word karaoke (native highlight only).
      if (this._highlighter?.native) {
        const first = this._highlighter.tokenRanges.get(chunk.tokens[0].index);
        const last = this._highlighter.tokenRanges.get(
          chunk.tokens[chunk.tokens.length - 1].index
        );
        if (first && last) {
          const r = document.createRange();
          try {
            r.setStart(first.startContainer, first.startOffset);
            r.setEnd(last.endContainer, last.endOffset);
            this._highlighter.setSentence(r);
          } catch { /* straddles weird markup — skip tint */ }
        }
      }
    };
    e.onEnd = () => this._finish();
    e.onError = (err) => {
      this._announce(`Read-along error: ${err.message}`);
      this._setStatus("Error");
      this._setPlayingUi(false);
      this._state = "idle";
    };
    e.onMode = (mode) => {
      if (mode !== "visual") return;
      this._setStatus("Visual mode — no voices");
      this._announce("No speech voices are available in this browser. Following the words without sound.");
    };
    if (this._chunks.length) e.setChunks?.(this._chunks);
  }

  _play() {
    this._prepare();
    if (!this._engine) return;
    // One-voice policy: starting this player stops any other on the page.
    for (const other of ACTIVE) if (other !== this) other.stop();
    ACTIVE.add(this);
    if (this._state === "paused") {
      this._state = "playing";
      this._engine.resume();
      this._setPlayingUi(true);
      this._emitEvent("play"); // resumed
      return;
    }
    if (this._state === "playing") return;
    if (!this._chunks.length) return;
    this._state = "playing";
    this._bindEngine();
    this._engine.rate = parseFloat(this._els.speed.value) || 1;
    this._engine.speak(this._chunks);
    this._setPlayingUi(true);
    this._announce("Playing, automated voice");
    this._emitEvent("play");
  }

  _pause() {
    if (this._state !== "playing") return;
    this._state = "paused";
    this._engine.pause();
    this._setPlayingUi(false);
    this._announce("Paused");
    this._emitEvent("pause");
  }

  _stop() {
    ACTIVE.delete(this);
    if (this._state === "idle") return;
    const wasEngine = this._engine;
    this._state = "idle";
    wasEngine?.stop?.();
    this._highlighter?.clear();
    this._setPlayingUi(false);
    this._setStatus("Ready");
    this._emitEvent("stop");
  }

  _finish() {
    ACTIVE.delete(this);
    this._state = "idle";
    this._highlighter?.clear();
    this._setPlayingUi(false);
    this._setStatus("Done");
    this._announce("Finished reading");
    this._emitEvent("done");
  }

  /** Hosts (e.g. an external-clock bridge) listen to these to stay in sync. */
  _emitEvent(name) {
    this.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: { token: this._engine?.position } }));
  }

  // -- UI helpers ----------------------------------------------------------

  _setPlayingUi(on) {
    this._els.play.setAttribute("aria-pressed", String(on));
    this._els.playicon.setAttribute("d", on ? ICON_PAUSE : ICON_PLAY);
    this._els.playlabel.textContent = on ? "Pause" : "Listen";
    this._setStatus(on ? "Playing" : "Paused");
  }

  _setStatus(msg) { this._els.status.textContent = msg; }

  _announce(msg) { this._els.live.textContent = msg; }
}

if (!customElements.get("read-along")) {
  customElements.define("read-along", ReadAlong);
}

export { ReadAlong, WebSpeechEngine, tokenize, chunkTokens, Highlighter, buildTokenRanges, supportsHighlightAPI };