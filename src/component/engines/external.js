/**
 * external.js — external-clock engine: the host owns speech, the component
 * only renders.
 *
 * Use when something OUTSIDE the browser is producing the audio/timing:
 * the ReadAloudTTS desktop daemon (Piper + AHK) streaming word timings, a
 * native app TTS bridge, a BCI/eye-gaze layer, or simply timings computed
 * at build time with no audio at all (visual-only karaoke).
 *
 * The host supplies word timings once, then ticks a monotonic clock:
 *
 *   const engine = new ExternalEngine({
 *     words: [[tokenIndex, startMs, endMs], ...]  // sorted by startMs
 *   });
 *   readAlong.engine = engine;
 *   readAlong.play();            // engine.speak() starts the rAF clock
 *   engine.tick(1234);           // host calls this with elapsed ms
 *
 * `tick(ms)` only advances the word pointer — it never seeks backwards on
 * its own (hosts send monotonic clocks), but a jump larger than a word is
 * followed, so a host that restarts at an earlier position works too.
 * When the host is silent, the engine falls back to real time, so the
 * karaoke never stalls waiting for a bridge that died.
 *
 * Timings format matches the MediaEngine manifest (`[tokenIndex, startMs,
 * endMs]`) — one producer can feed both engines.
 */

const HOST_SILENT_MS = 250; // no tick for this long → advance on real time

export class ExternalEngine {
  /**
   * @param {{words?: Array<[number, number, number]>, onTick?: Function}} [options]
   */
  constructor(options = {}) {
    this.words = (options.words ?? []).slice().sort((a, b) => a[1] - b[1]);
    this.onToken = options.onToken || null;
    this.onChunkStart = options.onChunkStart || null;
    this.onChunkEnd = options.onChunkEnd || null;
    this.onEnd = options.onEnd || null;
    this.onError = options.onError || null;
    this.onMode = options.onMode || null;
    this._chunks = [];
    this._stopped = true;
    this._paused = false;
    this._raf = 0;
    this._token = -1;      // last token index emitted
    this._clock = 0;       // ms position, host-driven when ticking
    this._lastTickAt = 0;  // performance.now() of the last host tick
    this._basePerf = 0;    // performance.now() at speak/resume — real-time base
  }

  /** Late-arriving or updated timings (host synthesizes progressively). */
  setWords(words) {
    this.words = (words ?? []).slice().sort((a, b) => a[1] - b[1]);
    if (!this._stopped && this.words.length) {
      // A late word may already be behind the clock — re-emit if so.
      const w = this._find(this._clock);
      if (w >= 0 && w !== this._token) this._emit(w);
    }
  }

  setChunks(chunks) { this._chunks = chunks || []; }

  get rate() { return 1; }
  set rate(_r) { /* external clock: speed belongs to the host */ }

  speak(_chunks, startWord = 0) {
    this._stopped = false;
    this._paused = false;
    this._startClock(startWord);
  }

  /** Host heartbeat: elapsed playback milliseconds (monotonic, may jump). */
  tick(ms) {
    if (this._stopped || this._paused) return;
    this._lastTickAt = performance.now(); // host is alive
    this._advance(ms);
  }

  pause() {
    if (this._stopped || this._paused) return;
    this._paused = true;
    this._cancelRaf();
  }

  resume() {
    if (this._stopped || !this._paused) return;
    this._paused = false;
    this._lastTickAt = performance.now();
    this._basePerf = performance.now() - this._clock;
    this._armRaf();
  }

  stop() {
    if (this._stopped) return;
    this._stopped = true;
    this._cancelRaf();
    this.onEnd?.();
  }

  get position() { return this._token; }

  // -- internals -----------------------------------------------------------

  /** Move the clock to `ms` and emit any word transitions it crosses. */
  _advance(ms) {
    if (this._stopped || this._paused) return;
    this._clock = Math.max(this._clock, ms);
    const w = this._find(this._clock);
    if (w >= 0 && w !== this._token) this._emit(w);
    // Past the last word's end (+ grace) → utterance over.
    const last = this.words[this.words.length - 1];
    if (last && this._clock > last[2] + HOST_SILENT_MS) {
      this._stopped = true;
      this._cancelRaf();
      this.onEnd?.();
    }
  }

  _startClock(startWord = 0) {
    const w = this.words.find(([tok]) => tok === startWord);
    this._clock = w ? w[1] : 0;
    this._token = startWord > 0 ? startWord : -1;
    this._basePerf = performance.now() - this._clock;
    this._lastTickAt = performance.now();
    const chunkIdx = this._chunkOf(startWord);
    if (chunkIdx >= 0) this.onChunkStart?.(chunkIdx, this._chunks[chunkIdx] ?? null);
    this._emit(startWord > 0 ? startWord : this._find(this._clock));
    this._armRaf();
  }

  _emit(i) {
    if (i < 0) return;
    const prev = this._token;
    this._token = i;
    this.onToken?.(i);
    const pc = this._chunkOf(prev);
    const cc = this._chunkOf(i);
    if (pc >= 0 && pc !== cc) this.onChunkEnd?.(pc, this._chunks[pc] ?? null);
    if (cc >= 0 && cc !== pc) this.onChunkStart?.(cc, this._chunks[cc] ?? null);
    // Past the last word's end → utterance over.
    const last = this.words[this.words.length - 1];
    if (last && this._clock > last[2] + HOST_SILENT_MS) {
      this._stopped = true;
      this._cancelRaf();
      this.onEnd?.();
    }
  }

  _find(ms) {
    let idx = -1;
    for (const [tok, startMs] of this.words) {
      if (startMs <= ms) idx = tok;
      else break;
    }
    return idx;
  }

  _chunkOf(tokenIndex) {
    if (tokenIndex < 0 || !this._chunks.length) return -1;
    for (let i = 0; i < this._chunks.length; i++) {
      const toks = this._chunks[i].tokens;
      if (tokenIndex >= toks[0].index && tokenIndex <= toks[toks.length - 1].index) {
        return i;
      }
    }
    return -1;
  }

  _armRaf() {
    if (this._raf) return;
    const step = () => {
      this._raf = 0;
      if (this._stopped || this._paused) return;
      const hostLive = performance.now() - this._lastTickAt < HOST_SILENT_MS;
      if (hostLive) {
        this._advance(this._clock); // host driving — check for end-of-words
      } else {
        this._advance(performance.now() - this._basePerf); // host silent → real time
      }
      if (!this._stopped && !this._paused) this._armRaf();
    };
    this._raf = requestAnimationFrame(step);
  }

  _cancelRaf() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = 0;
  }
}