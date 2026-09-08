/**
 * webspeech.js — Web Speech API engine with the Chrome cutoff defeated.
 *
 * Failure modes handled:
 *  1. The ~15s watchdog in desktop Chrome kills long utterances
 *     (chromium:41294170, ~200-250 chars). Defeat: every utterance stays
 *     under the cap via sentence-bounded chunking, plus a desktop-only
 *     pause()/resume() keep-alive (it breaks speech on Android).
 *  2. onboundary is an optimization, not a sync source: it never fires for
 *     remote voices, fails on Chrome Android, fires sparsely on Safari,
 *     effectively never on iOS. Defeat: char-proportional interpolation
 *     (the proven ReadAloudTTS approach) driven by rAF when boundaries
 *     stay silent for BOUNDARY_GRACE_MS.
 *  3. Dead engines: embedded browsers (CEF/webviews) often expose
 *     speechSynthesis with ZERO voices — speak() is a silent no-op, no
 *     start/boundary/end ever fires. Defeat: a stall watchdog — if nothing
 *     has progressed within STALL_MS, the engine switches to VISUAL-ONLY
 *     mode: karaoke word pacing without audio, announced via onMode, so
 *     the read-along still works (and never hangs in "Playing" forever).
 */

const KEEPALIVE_MS = 10_000;
const BOUNDARY_GRACE_MS = 600;
const STALL_MS = 2_800;
const CHARS_PER_SEC = 14.5; // ~150 wpm × ~5.8 chars/word, heuristic at rate 1

export class WebSpeechEngine {
  constructor(options = {}) {
    this.lang = options.lang ?? navigator.language ?? "en-US";
    this.rate = options.rate ?? 1;
    this.pitch = options.pitch ?? 1;
    this.voiceName = options.voiceName ?? null;
    this.onToken = options.onToken || null;
    this.onChunkStart = options.onChunkStart || null;
    this.onChunkEnd = options.onChunkEnd || null;
    this.onEnd = options.onEnd || null;
    this.onError = options.onError || null;
    this.onVoices = options.onVoices || null;
    this.onMode = options.onMode || null; // 'visual' when audio is unavailable
    this._voices = [];
    this._keepalive = null;
    this._raf = null;
    this._graceTimer = null;
    this._stallTimer = null;
    this._stopped = true;
    this._paused = false;
    this._visualOnly = false;
    this._interpActive = false;
    this._interpElapsed = 0;
    this._vraf = null;
    this._vT0 = 0;
    this._chunkIdx = -1;
    this._tokenIndex = -1;
    this._chunks = [];
    if (WebSpeechEngine.available) {
      this._refreshVoices();
      speechSynthesis.onvoiceschanged = () => this._refreshVoices();
    }
  }

  static get available() {
    return typeof window !== "undefined" && "speechSynthesis" in window;
  }

  _refreshVoices() {
    this._voices = speechSynthesis.getVoices() || [];
    if (this._voices.length) this.onVoices?.(this._voices);
  }

  get voices() {
    return this._voices;
  }

  /** Engine contract: register chunks without speaking. */
  setChunks(chunks) {
    this._chunks = chunks || [];
  }

  /**
   * Speak chunks sequentially. Each chunk is one short utterance.
   * @param {Array<{tokens:Array,start:number,end:number}>} chunks
   * @param {number} startWord global token index to start from (seek)
   */
  speak(chunks, startWord = 0) {
    this._chunks = chunks;
    this._pendingSlice = null;
    let startChunk = 0;
    if (startWord > 0) {
      const ci = locateTokenChunk(chunks, startWord);
      if (ci >= 0) {
        startChunk = ci;
        this._pendingSlice = startWord;
      }
    }
    if (!WebSpeechEngine.available) {
      // No speechSynthesis at all (Firefox Android) — straight to visual.
      this._stopped = false;
      this._paused = false;
      this._engageVisualOnly();
      return;
    }
    this._stopped = false;
    this._paused = false;
    this._visualOnly = false;
    this._progress = false;
    this._cancelVisual();
    this._startKeepalive();
    this._speakChunk(startChunk);
    this._startStallWatchdog();
  }

  /**
   * The chunk to speak for index i, consuming a pending seek slice. A seek
   * into mid-chunk swaps in a sliced copy (tokens from the seek word on);
   * tokens keep their GLOBAL .index so highlights still map. The slice is
   * one-shot: the next _speakChunk(i+1) gets the full chunk.
   */
  _chunkFor(i) {
    const chunk = this._chunks[i];
    if (chunk && this._pendingSlice != null) {
      const from = this._pendingSlice;
      this._pendingSlice = null;
      const k = chunk.tokens.findIndex((t) => t.index === from);
      if (k > 0) {
        return { tokens: chunk.tokens.slice(k), start: chunk.tokens[k].start, end: chunk.end };
      }
    }
    return chunk;
  }

  _speakChunk(i) {
    if (this._stopped || this._visualOnly) return;
    this._cancelInterpolation();
    if (i >= this._chunks.length) {
      this._finish();
      return;
    }
    this._chunkIdx = i;
    const chunk = this._chunkFor(i);
    const utter = new SpeechSynthesisUtterance(chunkText(chunk));
    utter.lang = this.lang;
    utter.rate = this.rate;
    utter.pitch = this.pitch;
    const voice = this._pickVoice();
    if (voice) {
      utter.voice = voice;
      utter.lang = voice.lang;
    }
    this.onChunkStart?.(i, chunk);

    let boundarySeen = false;
    this._tokenIndex = -1;
    this._chunkT0 = performance.now();

    utter.onboundary = (e) => {
      if (this._stopped || this._paused || this._visualOnly) return;
      if (e.name && e.name !== "word") return;
      boundarySeen = true;
      this._progress = true;
      this._clearGrace();
      this._cancelInterpolation();
      const tok = tokenAtChar(chunk, e.charIndex ?? 0);
      if (tok) this._emitToken(tok.index);
    };

    utter.onstart = () => {
      if (this._stopped || this._visualOnly) return;
      this._progress = true;
    };

    utter.onend = () => {
      if (this._stopped || this._visualOnly) return;
      this._progress = true;
      this._clearGrace();
      this._cancelInterpolation();
      this.onChunkEnd?.(i, chunk);
      this._speakChunk(i + 1);
    };

    utter.onerror = (ev) => {
      // cancel() surfaces as interrupted/canceled — a clean stop, not an error.
      const err = ev?.error ?? "unknown";
      if (err === "interrupted" || err === "canceled" || err === "Canceled") return;
      this._progress = true;
      this._clearGrace();
      this._cancelInterpolation();
      this._stopKeepalive();
      this.onError?.(new Error(`speech synthesis error: ${err}`));
    };

    speechSynthesis.speak(utter);

    // If this voice never fires boundaries, interpolate word timing.
    this._armGrace(chunk, i, boundarySeen);
  }

  /**
   * After BOUNDARY_GRACE_MS without a word boundary, take over word
   * timing with char-proportional interpolation. Re-armed on resume()
   * in case the window elapsed while paused (otherwise a boundary-silent
   * voice would leave the highlight frozen for the rest of the chunk).
   */
  _armGrace(chunk, i, hadBoundary) {
    this._clearGrace();
    this._graceTimer = setTimeout(() => {
      if (!this._stopped && !this._paused && !hadBoundary &&
          !this._visualOnly && this._chunkIdx === i) {
        this._startInterpolation(chunk, i);
      }
    }, BOUNDARY_GRACE_MS);
  }

  _emitToken(globalIdx) {
    if (globalIdx === this._tokenIndex) return;
    this._tokenIndex = globalIdx;
    this.onToken?.(globalIdx);
  }

  _startInterpolation(chunk, chunkIdx) {
    this._cancelRaf();
    this._interpActive = true;
    this._chunkT0 = performance.now() - this._interpElapsed;
    const step = () => {
      if (this._stopped || this._paused || this._chunkIdx !== chunkIdx) return;
      const elapsed = (performance.now() - this._chunkT0) / 1000;
      const chars = elapsed * CHARS_PER_SEC * this.rate;
      const tok = tokenAtChar(chunk, Math.floor(chars));
      if (tok) this._emitToken(tok.index);
      this._raf = requestAnimationFrame(step);
    };
    this._raf = requestAnimationFrame(step);
  }

  /** Stop the rAF loop but KEEP interpolation mode + frozen offset. */
  _cancelRaf() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  /** Leave interpolation mode entirely (boundary takeover, chunk end, stop). */
  _cancelInterpolation() {
    this._cancelRaf();
    this._interpActive = false;
    this._interpElapsed = 0;
  }

  // -- stall watchdog → visual-only mode -----------------------------------

  _startStallWatchdog() {
    this._clearStall();
    this._stallTimer = setTimeout(() => {
      if (this._stopped || this._paused || this._visualOnly || this._progress) return;
      // speak() was a silent no-op (typical zero-voice embedded browser):
      // nothing is speaking and nothing is even queued.
      if (!speechSynthesis.speaking && !speechSynthesis.pending) {
        this._engageVisualOnly();
      }
    }, STALL_MS);
  }

  _clearStall() {
    if (this._stallTimer) clearTimeout(this._stallTimer);
    this._stallTimer = null;
  }

  _clearGrace() {
    if (this._graceTimer) clearTimeout(this._graceTimer);
    this._graceTimer = null;
  }

  /** Audio is dead — run karaoke word pacing without sound. */
  _engageVisualOnly() {
    this._visualOnly = true;
    this._cancelInterpolation();
    this._clearGrace();
    this._stopKeepalive();
    if (WebSpeechEngine.available) {
      try { speechSynthesis.cancel(); } catch { /* nothing to cancel */ }
    }
    this.onMode?.("visual");
    let i = this._chunkIdx >= 0 ? this._chunkIdx : 0;
    if (this._pendingSlice != null) {
      const ci = locateTokenChunk(this._chunks, this._pendingSlice);
      if (ci >= 0) i = ci;
    }
    this._visualChunk(i);
  }

  _visualChunk(i) {
    if (this._stopped || !this._visualOnly) return;
    if (i >= this._chunks.length) {
      this._finish();
      return;
    }
    this._chunkIdx = i;
    const chunk = this._chunkFor(i);
    this.onChunkStart?.(i, chunk);
    const text = chunkText(chunk);
    const durMs = (text.length / (CHARS_PER_SEC * this.rate)) * 1000;
    this._vT0 = performance.now();
    this._tokenIndex = -1;
    const step = () => {
      if (this._stopped || this._paused || !this._visualOnly || this._chunkIdx !== i) return;
      const elapsed = performance.now() - this._vT0;
      if (elapsed >= durMs) {
        this.onChunkEnd?.(i, chunk);
        this._visualChunk(i + 1);
        return;
      }
      const chars = Math.floor((elapsed / 1000) * CHARS_PER_SEC * this.rate);
      const tok = tokenAtChar(chunk, chars);
      if (tok) this._emitToken(tok.index);
      this._vraf = requestAnimationFrame(step);
    };
    this._vraf = requestAnimationFrame(step);
  }

  _cancelVisual() {
    if (this._vraf) cancelAnimationFrame(this._vraf);
    this._vraf = null;
  }

  // -- shared plumbing ------------------------------------------------------

  _pickVoice() {
    if (!this._voices.length) return null;
    if (this.voiceName) {
      const named = this._voices.find((v) => v.name === this.voiceName);
      if (named) return named;
    }
    const base = this.lang.split("-")[0];
    return (
      this._voices.find((v) => v.lang === this.lang) ||
      this._voices.find((v) => v.lang?.startsWith(base)) ||
      null
    );
  }

  _startKeepalive() {
    this._stopKeepalive();
    // Desktop-Chrome-only belt-and-suspenders for the ~15s watchdog
    // (chromium:41294170). On Android, pause()/resume() mid-utterance
    // breaks synthesis entirely — sentence chunking alone is the fix there.
    if (/Android/i.test(navigator.userAgent)) return;
    this._keepalive = setInterval(() => {
      if (this._stopped || this._paused || this._visualOnly) return;
      if (speechSynthesis.speaking && !speechSynthesis.paused) {
        speechSynthesis.pause();
        speechSynthesis.resume();
      }
    }, KEEPALIVE_MS);
  }

  _stopKeepalive() {
    if (this._keepalive) clearInterval(this._keepalive);
    this._keepalive = null;
  }

  _finish() {
    this._stopKeepalive();
    this._clearGrace();
    this._clearStall();
    this._cancelInterpolation();
    this._cancelVisual();
    this.onEnd?.();
  }

  pause() {
    if (this._stopped || this._paused) return;
    this._paused = true;
    if (this._visualOnly) {
      this._vElapsedVisual = this._vT0 ? performance.now() - this._vT0 : 0;
      this._cancelVisual();
    } else {
      // Freeze the interpolation clock but KEEP the mode: resume() restarts
      // the rAF from this offset (_cancelInterpolation would zero it).
      if (this._interpActive) this._interpElapsed = performance.now() - this._chunkT0;
      this._cancelRaf();
      speechSynthesis.pause();
    }
  }

  resume() {
    if (!this._paused) return;
    this._paused = false;
    if (this._visualOnly) {
      // Continue visual pacing from where it froze.
      const i = this._chunkIdx;
      const chunk = this._chunks[i];
      if (!chunk) return;
      const text = chunkText(chunk);
      const durMs = (text.length / (CHARS_PER_SEC * this.rate)) * 1000;
      const frozen = Math.min(this._vElapsedVisual ?? 0, durMs);
      this._vT0 = performance.now() - frozen;
      this._tokenIndex = -1;
      const step = () => {
        if (this._stopped || !this._visualOnly || this._chunkIdx !== i) return;
        const elapsed = performance.now() - this._vT0;
        if (elapsed >= durMs) {
          this.onChunkEnd?.(i, chunk);
          this._visualChunk(i + 1);
          return;
        }
        const chars = Math.floor((elapsed / 1000) * CHARS_PER_SEC * this.rate);
        const tok = tokenAtChar(chunk, chars);
        if (tok) this._emitToken(tok.index);
        this._vraf = requestAnimationFrame(step);
      };
      this._vraf = requestAnimationFrame(step);
    } else {
      speechSynthesis.resume();
      const chunk = this._chunks[this._chunkIdx];
      if (this._interpActive && chunk) {
        // Continue the interpolation clock from the frozen offset.
        this._startInterpolation(chunk, this._chunkIdx);
      } else if (chunk && !this._progress) {
        // Engine was dead all along (no voices): re-arm the stall watchdog.
        this._startStallWatchdog();
      } else if (chunk) {
        // Grace window elapsed while paused on a boundary-silent voice —
        // re-arm or the highlight freezes for the rest of this chunk.
        this._chunkT0 = performance.now();
        this._armGrace(chunk, this._chunkIdx, false);
      }
    }
  }

  stop() {
    if (this._stopped) return;
    this._stopped = true;
    this._stopKeepalive();
    this._clearGrace();
    this._clearStall();
    this._cancelInterpolation();
    this._cancelVisual();
    if (WebSpeechEngine.available) {
      try { speechSynthesis.cancel(); } catch { /* not speaking — fine */ }
    }
    this.onEnd?.();
  }

  get position() {
    return { chunk: this._chunkIdx, token: this._tokenIndex };
  }

  get mode() {
    return this._visualOnly ? "visual" : "audio";
  }
}

/** Join a chunk's tokens back into speakable text. */
export function chunkText(chunk) {
  return chunk.tokens.map((t) => t.text).join(" ");
}

/** Chunk index containing global token index `word` (-1 if not found). */
export function locateTokenChunk(chunks, word) {
  for (let i = 0; i < chunks.length; i++) {
    const toks = chunks[i].tokens;
    if (word >= toks[0].index && word <= toks[toks.length - 1].index) return i;
  }
  return -1;
}

/** Token whose chunk-local [offset, offset+len) contains charIndex. */
export function tokenAtChar(chunk, charIndex) {
  let offset = 0;
  for (const tok of chunk.tokens) {
    if (charIndex >= offset && charIndex <= offset + tok.text.length) return tok;
    offset += tok.text.length + 1;
  }
  return chunk.tokens.length ? chunk.tokens[chunk.tokens.length - 1] : null;
}