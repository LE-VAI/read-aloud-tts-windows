/**
 * highlight.js — karaoke highlight over arbitrary inline markup.
 *
 * Primary path: CSS Custom Highlight API (Highlight + CSS.highlights +
 * ::highlight() pseudo) — paints a Range without touching the DOM, so host
 * markup (links, emphasis, listeners) stays intact.
 *
 * The registry (CSS.highlights) is PAGE-WIDE and keyed by name. Two
 * <read-along> elements on one page must therefore SHARE one Highlight
 * object per name: if each instance registered its own, the second
 * constructor would silently REPLACE the first's object, and every range
 * added to the orphaned object would never paint — the first element's
 * highlight dies with no error anywhere. Highlight objects are thus
 * module-level singletons, refcounted across instances; each instance
 * adds/removes only the Ranges it owns.
 *
 * Fallback path: wrap the active word in <mark data-read-along> for engines
 * without the Highlight API, or when forced via the component's
 * force-fallback option (for engines that expose the registry but never
 * paint it — there is no way to detect that programmatically).
 */

const HL_WORD = "read-along-word";
const HL_SENTENCE = "read-along-sentence";
const FALLBACK_TAG = "mark";

/** name -> { highlight: Highlight, refs: number } */
const SHARED = new Map();

function acquire(name) {
  let entry = SHARED.get(name);
  if (!entry) {
    const highlight = new Highlight();
    CSS.highlights.set(name, highlight);
    entry = { highlight, refs: 0 };
    SHARED.set(name, entry);
  }
  entry.refs++;
  return entry;
}

function release(name) {
  const entry = SHARED.get(name);
  if (!entry) return;
  entry.refs--;
  if (entry.refs <= 0) {
    try { CSS.highlights.delete(name); } catch { /* registry gone */ }
    SHARED.delete(name);
  }
}

export function supportsHighlightAPI() {
  return (
    typeof Highlight !== "undefined" &&
    typeof CSS !== "undefined" &&
    typeof CSS.highlights !== "undefined" &&
    CSS.highlights instanceof HighlightRegistry
  );
}

/**
 * Map token text-offsets to DOM Ranges by walking the host's text nodes once.
 * Offsets are into the concatenated text-node content (what tokenize() saw).
 * A token that straddles a text-node boundary maps to its first fragment only.
 * @returns {Map<number, Range>} token index -> Range
 */
export function buildTokenRanges(host, tokens) {
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_TEXT);
  const ranges = new Map();
  let node = walker.nextNode();
  let nodeStart = 0;
  for (const tok of tokens) {
    while (node !== null && nodeStart + node.data.length <= tok.start) {
      nodeStart += node.data.length;
      node = walker.nextNode();
    }
    if (node === null) break; // tokens outlive the DOM text — stop
    const localStart = Math.max(0, tok.start - nodeStart);
    const localEnd = Math.min(node.data.length, tok.end - nodeStart);
    if (localEnd <= localStart) continue;
    const r = document.createRange();
    r.setStart(node, localStart);
    r.setEnd(node, localEnd);
    ranges.set(tok.index, r);
  }
  return ranges;
}

export class Highlighter {
  /**
   * @param {Element} host element whose light-DOM text is tokenized
   * @param {{forceFallback?: boolean}} [options]
   */
  constructor(host, { forceFallback = false } = {}) {
    this.host = host;
    this.native = !forceFallback && supportsHighlightAPI();
    this.destroyed = false;
    this.tokenRanges = new Map();
    // Ranges this instance currently owns inside the shared Highlight objects.
    this._wordRanges = [];
    this._sentenceRanges = [];
    this.markEl = null;
    this._markIndex = -2;
    if (this.native) {
      this._wordEntry = acquire(HL_WORD);
      this._sentenceEntry = acquire(HL_SENTENCE);
    }
  }

  /** Registered shared Highlight objects (introspection/testing handle). */
  get wordHighlight() { return this._wordEntry?.highlight ?? null; }
  get sentenceHighlight() { return this._sentenceEntry?.highlight ?? null; }

  setTokenRanges(ranges) {
    this.tokenRanges = ranges;
  }

  /** Highlight token index i as the active word; clear the previous word. */
  setActive(i) {
    if (this.native) {
      const hl = this._wordEntry.highlight;
      for (const r of this._wordRanges) hl.delete(r);
      this._wordRanges.length = 0;
      const r = this.tokenRanges.get(i);
      if (r) {
        hl.add(r);
        this._wordRanges.push(r);
      }
    } else {
      this._fallbackMark(i);
    }
  }

  /**
   * Sentence-track tint: keep the current chunk's range softly highlighted
   * while the word pointer moves through it. Range or null.
   */
  setSentence(range) {
    if (!this.native) return;
    const hl = this._sentenceEntry.highlight;
    for (const r of this._sentenceRanges) hl.delete(r);
    this._sentenceRanges.length = 0;
    if (range) {
      hl.add(range);
      this._sentenceRanges.push(range);
    }
  }

  clear() {
    if (this.native) {
      for (const r of this._wordRanges) this._wordEntry.highlight.delete(r);
      for (const r of this._sentenceRanges) this._sentenceEntry.highlight.delete(r);
      this._wordRanges.length = 0;
      this._sentenceRanges.length = 0;
    }
    this._unwrapMark();
  }

  destroy() {
    this.clear();
    if (this.native) {
      release(HL_WORD);
      release(HL_SENTENCE);
      this._wordEntry = null;
      this._sentenceEntry = null;
      this.native = false;
    }
    this.destroyed = true;
  }

  // -- fallback path ------------------------------------------------------

  _fallbackMark(i) {
    if (i === this._markIndex) return;
    const r = this.tokenRanges.get(i);
    this._unwrapMark();
    this._markIndex = -2;
    if (!r) return;
    try {
      const contents = r.extractContents();
      const mark = document.createElement(FALLBACK_TAG);
      mark.dataset.readAlong = "";
      this._markIndex = i;
      mark.appendChild(contents);
      r.insertNode(mark);
      this.markEl = mark;
    } catch { /* DOM changed mid-speech — skip this token */ }
  }

  _unwrapMark() {
    const mark = this.markEl;
    if (!mark) return;
    const parent = mark.parentNode;
    if (!parent) {
      this.markEl = null;
      return;
    }
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parent.normalize();
    this.markEl = null;
    this._markIndex = -2;
  }
}