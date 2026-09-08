/**
 * tokenizer.js — whitespace tokenization that preserves source offsets.
 *
 * Tokens carry [start, end) offsets into the original text so the highlight
 * layer can build DOM Ranges without re-wrapping the host's markup. Works on
 * the text content of the host element; the element's own children (links,
 * emphasis) are allowed and ranges are computed against them.
 */

const WORD_RE = /\S+/g;

/**
 * @param {string} text
 * @returns {Array<{text: string, start: number, end: number, index: number}>}
 */
export function tokenize(text) {
  const tokens = [];
  WORD_RE.lastIndex = 0;
  let m;
  while ((m = WORD_RE.exec(text)) !== null) {
    tokens.push({
      text: m[0],
      start: m.index,
      end: m.index + m[0].length,
      index: tokens.length,
    });
  }
  return tokens;
}

/**
 * @param {string} text
 * @returns {Array<{text: string, start: number, end: number}>} sentence spans
 */
export function sentences(text) {
  const out = [];
  const re = /[^.!?\n]+[.!?]*[\s]*/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[0].trim().length > 0) {
      out.push({ text: m[0], start: m.index, end: m.index + m[0].length });
    }
  }
  return out;
}

const SENTENCE_END = /[.!?]["')\]]?$/;

/**
 * Group tokens into speakable chunks: sentence-bounded, char-capped.
 *
 * Chunks ACCUMULATE sentences up to the char cap (the ReadAloudTTS lesson:
 * one-chunk-per-sentence means 3-5x more chunks than necessary and, for
 * streaming engines, 3-5x more synthesis calls). Emission rules:
 *   - emit when the cap is reached AND the current token closes a sentence
 *   - emit anyway at 1.5x the cap (hard overflow guard for run-on text)
 *   - chunk 0 is capped at `firstChunkChars` so audio starts fast
 *
 * @param {Array<{text:string,start:number,end:number}>} tokens
 * @param {number} chunkChars max chars per chunk (chunks 1+)
 * @param {number} firstChunkChars cap for chunk 0 (0 = same as chunkChars)
 * @returns {Array<{tokens: Array, start: number, end: number}>}
 */
export function chunkTokens(tokens, chunkChars = 180, firstChunkChars = 80) {
  if (!tokens.length) return [];
  const limitFor = (i) => (i === 0 && firstChunkChars > 0 ? firstChunkChars : chunkChars);
  const chunks = [];
  let current = [];
  let currentChars = 0;
  let chunkIndex = 0;

  const emit = () => {
    if (!current.length) return;
    chunks.push({
      tokens: current,
      start: current[0].start,
      end: current[current.length - 1].end,
    });
    current = [];
    currentChars = 0;
    chunkIndex++;
  };

  for (const tok of tokens) {
    const limit = limitFor(chunkIndex);
    const endsSentence = SENTENCE_END.test(tok.text);
    if (current.length) {
      if (currentChars >= limit && endsSentence) {
        emit();
      } else if (currentChars >= limit * 1.5) {
        // Run-on text with no sentence punctuation — hard-emit at 1.5x.
        emit();
      }
    }
    current.push(tok);
    currentChars += tok.text.length + 1;
    // Fast start: chunk 0 emits the moment it hits its own smaller cap,
    // even mid-sentence — audio starting fast beats sentence purity.
    if (chunkIndex === 0 && firstChunkChars > 0 && currentChars >= firstChunkChars) {
      emit();
    }
  }
  emit();
  return chunks;
}