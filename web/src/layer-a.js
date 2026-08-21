/* Layer A in the browser: scan and clean invisible-character watermarks.
 *
 * This is a hand port of scrubai/layer_a.py and scrubai/unicode_tables.py. The
 * Python side is the reference implementation; the codepoint tables here are
 * GENERATED from it (see tables.js) and both runtimes are pinned to the same
 * conformance vectors, so a divergence fails CI before it can ship.
 *
 * If you change classification behaviour, change it in Python first.
 *
 * Everything below operates on codepoints instead of UTF-16 units, so astral
 * characters (emoji, supplementary tags and variation selectors) are handled
 * as single characters exactly as Python does.
 */

import {
  ALWAYS_KEEP, BIDI, CONFUSABLES, DEPRECATED_FORMAT, EMOJI_BASE,
  HANGUL_FILLERS, HANGUL_JAMO, INTERLINEAR, INVISIBLE_OPERATORS,
  KHMER_BLOCK, KHMER_VOWEL_INHERENT, MISC_STRIP, MONGOLIAN_FVS,
  MONGOLIAN_LETTERS, NAMES, PRIVATE_USE, REGIONAL_INDICATORS,
  SPACE_HOMOGLYPHS, TAG_CHARS, VARIATION_SELECTORS, ZWJ_FAMILY,
} from './tables.js';

export const KIND_STRIP = 'strip';
export const KIND_BIDI = 'bidi';
export const KIND_TAG = 'tag_chars';
export const KIND_VS = 'variation_selector';
export const KIND_ZWJ = 'zwj_family';
export const KIND_PUA = 'private_use';
export const KIND_SPACE = 'space';
export const KIND_CONFUSABLE = 'confusable';
export const KIND_OTHER_CF = 'other_cf';

export const MAX_OFFSETS_REPORTED = 10;

const inRanges = (cp, ranges) => {
  for (const [lo, hi] of ranges) if (cp >= lo && cp <= hi) return true;
  return false;
};

const inPair = (cp, [lo, hi]) => cp >= lo && cp <= hi;

/* ── Unicode general category ──────────────────────────────────────────────
 * Python uses unicodedata.category(). We only need two questions: "is this a
 * format character (Cf)?" and "is this a letter or a mark?". Both are
 * answerable with RegExp Unicode property escapes, supported in every browser
 * that supports ES modules.
 */
const RE_CF = /\p{General_Category=Format}/u;
const RE_LETTER_OR_MARK = /[\p{L}\p{M}]/u;

const isFormat = (ch) => RE_CF.test(ch);
const isLetterOrMark = (ch) => RE_LETTER_OR_MARK.test(ch);

export function codepointName(cp) {
  const hit = NAMES[cp.toString(16).toUpperCase()];
  if (hit) return hit;
  if (inRanges(cp, PRIVATE_USE)) return '<private-use>';
  if (inRanges(cp, TAG_CHARS)) return '<tag character>';
  if (cp >= 0xfe00 && cp <= 0xfe0f) return `VARIATION SELECTOR-${cp - 0xfe00 + 1}`;
  if (cp >= 0xe0100 && cp <= 0xe01ef) return `VARIATION SELECTOR-${cp - 0xe0100 + 17}`;
  return '<unnamed>';
}

export const hexOf = (cp) =>
  'U+' + cp.toString(16).toUpperCase().padStart(4, '0');

/* ── classification ─────────────────────────────────────────────────────── */

/** Mirror of unicode_tables.classify. Returns null when unremarkable. */
export function classify(cp, aggressive = false) {
  if (inRanges(cp, ALWAYS_KEEP)) return null;

  if (inRanges(cp, SPACE_HOMOGLYPHS)) {
    return { kind: KIND_SPACE, replacement: ' ', confidence: 'informational' };
  }
  if (inRanges(cp, ZWJ_FAMILY)) {
    // U+200E and U+200F are directional marks. They are labelled bidi.
    const kind = cp === 0x200e || cp === 0x200f ? KIND_BIDI : KIND_ZWJ;
    return { kind, replacement: '', confidence: 'probable' };
  }
  if (inRanges(cp, BIDI)) {
    return { kind: KIND_BIDI, replacement: '', confidence: 'probable' };
  }
  if (inRanges(cp, TAG_CHARS)) {
    return { kind: KIND_TAG, replacement: '', confidence: 'probable' };
  }
  if (inRanges(cp, VARIATION_SELECTORS)) {
    return { kind: KIND_VS, replacement: '', confidence: 'probable' };
  }
  if (inRanges(cp, PRIVATE_USE)) {
    return { kind: KIND_PUA, replacement: '', confidence: 'probable' };
  }
  if (inRanges(cp, INVISIBLE_OPERATORS) || inRanges(cp, DEPRECATED_FORMAT)) {
    return { kind: KIND_OTHER_CF, replacement: '', confidence: 'probable' };
  }
  if (inRanges(cp, INTERLINEAR) || inRanges(cp, MISC_STRIP)) {
    return { kind: KIND_STRIP, replacement: '', confidence: 'probable' };
  }
  if (aggressive && CONFUSABLES.has(cp)) {
    return {
      kind: KIND_CONFUSABLE,
      replacement: CONFUSABLES.get(cp),
      confidence: 'informational',
    };
  }
  // Catch-all for format characters we did not enumerate.
  if (isFormat(String.fromCodePoint(cp))) {
    return { kind: KIND_OTHER_CF, replacement: '', confidence: 'probable' };
  }
  return null;
}

const isEmojiBase = (ch) => {
  if (!ch) return false;
  const cp = ch.codePointAt(0);
  return inRanges(cp, EMOJI_BASE) || inPair(cp, REGIONAL_INDICATORS);
};

/**
 * Mirror of unicode_tables.is_load_bearing.
 * `chars` is the codepoint array; `index` indexes into it.
 * Returns a reason string, or null.
 */
export function isLoadBearing(chars, index) {
  const cp = chars[index].codePointAt(0);
  const prev = index > 0 ? chars[index - 1] : null;
  const prevCp = prev ? prev.codePointAt(0) : null;

  // Emoji glue: ZWJ and VS15/16 directly after an emoji base.
  if ((cp === 0x200d || cp === 0xfe0e || cp === 0xfe0f) && isEmojiBase(prev)) {
    return 'emoji_sequence';
  }

  // Tag-sequence flags. Walk back over the whole tag run to find its anchor.
  // A run only counts if it starts after an emoji base. Without the walk-back,
  // each tag character after the first looks legitimate, because its
  // predecessor is also a tag character.
  if (inRanges(cp, TAG_CHARS)) {
    let j = index - 1;
    while (j >= 0 && inRanges(chars[j].codePointAt(0), TAG_CHARS)) j--;
    if (j >= 0 && isEmojiBase(chars[j])) return 'flag_tag_sequence';
  }

  // Script joiners: orthographic in Persian, Arabic, Devanagari and similar.
  if (
    (cp === 0x200c || cp === 0x200d) &&
    prevCp !== null &&
    prevCp > 0x7f &&
    isLetterOrMark(prev)
  ) {
    return 'script_joiner';
  }

  if (
    inPair(cp, MONGOLIAN_FVS) &&
    prevCp !== null &&
    inPair(prevCp, MONGOLIAN_LETTERS)
  ) {
    return 'mongolian_fvs';
  }

  if (
    inPair(cp, KHMER_VOWEL_INHERENT) &&
    prevCp !== null &&
    prevCp !== 0 &&
    inPair(prevCp, KHMER_BLOCK)
  ) {
    return 'khmer_inherent_vowel';
  }

  if (
    inPair(cp, HANGUL_FILLERS) &&
    prevCp !== null &&
    inRanges(prevCp, HANGUL_JAMO)
  ) {
    return 'hangul_jamo_filler';
  }

  return null;
}

/* ── scan ───────────────────────────────────────────────────────────────── */

export function scan(text, { aggressive = false, stripEmojiGlue = false } = {}) {
  const chars = Array.from(text);
  const found = new Map();

  for (let i = 0; i < chars.length; i++) {
    const cp = chars[i].codePointAt(0);
    if (cp >= 0x20 && cp < 0x7f) continue;

    const cls = classify(cp, aggressive);
    if (!cls) continue;

    let f = found.get(cp);
    if (!f) {
      f = {
        codepointInt: cp,
        codepoint: hexOf(cp),
        name: codepointName(cp),
        kind: cls.kind,
        replacement: cls.replacement,
        confidence: cls.confidence,
        count: 0,
        offsets: [],
        keptCount: 0,
        keptReasons: {},
      };
      found.set(cp, f);
    }

    f.count += 1;
    if (f.offsets.length < MAX_OFFSETS_REPORTED * 4) f.offsets.push(i);

    if (!stripEmojiGlue && cls.kind !== KIND_SPACE && cls.kind !== KIND_CONFUSABLE) {
      const reason = isLoadBearing(chars, i);
      if (reason) {
        f.keptCount += 1;
        f.keptReasons[reason] = (f.keptReasons[reason] || 0) + 1;
      }
    }
  }

  const findings = [...found.values()].sort(
    (a, b) => b.count - a.count || a.codepointInt - b.codepointInt,
  );

  const totalFlagged = findings.reduce((n, f) => n + f.count, 0);
  const totalRemovable = findings.reduce((n, f) => n + f.count - f.keptCount, 0);
  const byKind = {};
  for (const f of findings) {
    byKind[f.kind] = (byKind[f.kind] || 0) + (f.count - f.keptCount);
  }

  return {
    layer: 'A',
    inputLength: chars.length,
    aggressive,
    stripEmojiGlue,
    totalFlagged,
    totalRemovable,
    byKind,
    watermarkSuspected: findings.some(
      (f) => f.confidence === 'probable' && f.count > f.keptCount,
    ),
    findings,
  };
}

/* ── clean ──────────────────────────────────────────────────────────────── */

export function clean(
  text,
  { aggressive = false, stripEmojiGlue = false, normalize = false } = {},
) {
  const chars = Array.from(text);
  const out = [];
  const removed = {};
  const replaced = {};
  const kept = {};
  const bump = (obj, key) => { obj[key] = (obj[key] || 0) + 1; };

  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i];
    const cp = ch.codePointAt(0);
    if (cp >= 0x20 && cp < 0x7f) { out.push(ch); continue; }

    const cls = classify(cp, aggressive);
    if (!cls) { out.push(ch); continue; }

    if (!stripEmojiGlue && cls.kind !== KIND_SPACE && cls.kind !== KIND_CONFUSABLE) {
      const reason = isLoadBearing(chars, i);
      if (reason) { bump(kept, reason); out.push(ch); continue; }
    }

    const label = `${hexOf(cp)} ${codepointName(cp)}`;
    if (cls.replacement) {
      bump(replaced, label);
      out.push(cls.replacement);
    } else {
      bump(removed, label);
    }
  }

  let result = out.join('');
  if (normalize) result = result.normalize('NFKC');

  const sum = (o) => Object.values(o).reduce((a, b) => a + b, 0);
  return {
    layer: 'A',
    text: result,
    inputLength: chars.length,
    outputLength: Array.from(result).length,
    totalRemoved: sum(removed),
    totalReplaced: sum(replaced),
    removed,
    replaced,
    keptLoadBearing: kept,
    nfkcNormalized: normalize,
  };
}

/* ── diff ───────────────────────────────────────────────────────────────── */

/**
 * Character-level diff as a list of {op, text} runs.
 *
 * Python uses difflib.SequenceMatcher. Rather than port that, we exploit the
 * fact that cleaning only ever deletes or one-for-one substitutes: replaying
 * the classification produces an exact diff directly, with no heuristics.
 */
export function diffOps(original, opts = {}) {
  const { aggressive = false, stripEmojiGlue = false } = opts;
  const chars = Array.from(original);
  const ops = [];
  let run = '';

  const flushEqual = () => {
    if (run) { ops.push({ op: 'equal', text: run }); run = ''; }
  };

  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i];
    const cp = ch.codePointAt(0);
    let cls = null;
    if (!(cp >= 0x20 && cp < 0x7f)) cls = classify(cp, aggressive);

    if (!cls) { run += ch; continue; }

    if (!stripEmojiGlue && cls.kind !== KIND_SPACE && cls.kind !== KIND_CONFUSABLE) {
      if (isLoadBearing(chars, i)) { run += ch; continue; }
    }

    flushEqual();
    if (cls.replacement) {
      ops.push({ op: 'replace', text: ch, with: cls.replacement, kind: cls.kind, cp });
    } else {
      ops.push({ op: 'delete', text: ch, kind: cls.kind, cp });
    }
  }
  flushEqual();
  return ops;
}
