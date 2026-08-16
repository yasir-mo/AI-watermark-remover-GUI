/* Layer B in the browser.
 *
 * Prompts, divergence scoring and candidate selection mirror wmtk/rewrite.py
 * so the hosted demo and the local CLI behave the same way.
 *
 * The request goes from this page straight to the provider. The key is never
 * sent anywhere else -- there is no backend to send it to. You can confirm
 * that in the network tab: exactly one request, to the provider's own domain.
 */

import { clean as layerAClean } from './layer-a.js';

export const STRENGTHS = [
  {
    id: 'paraphrase',
    label: 'Paraphrase',
    blurb: 'Different wording, same shape and length.',
  },
  {
    id: 'humanize',
    label: 'Humanize',
    blurb: 'Different rhythm, no formulaic AI transitions.',
  },
  {
    id: 'code',
    label: 'Code',
    blurb: 'Comments and locals only. Behaviour and public API preserved.',
  },
  {
    id: 'backtranslate',
    label: 'Backtranslate',
    blurb: 'Through a pivot language and back. Two model calls.',
  },
  {
    id: 'structural',
    label: 'Structural',
    blurb: 'Extract an outline, then rewrite from it. Two model calls.',
  },
];

const PROMPTS = {
  paraphrase: `Rewrite the text below with substantially different wording at the token level.

Requirements:
- Change clause order, connectors, and transition words.
- Vary sentence boundaries and sentence length; split and merge sentences.
- Replace content words and function words wherever meaning allows.
- Preserve every fact, number, name, date, quotation, and technical identifier exactly.
- Keep the same language.
- Match the original length closely. The original is {source_words} words, so write
  between {min_words} and {max_words} words. Do not add material to pad it out.
- Output only the rewritten text. No preamble, no commentary, no quotation marks
  around it, and no explanation of what you changed.

TEXT:
{text}`,

  humanize: `Rewrite the text below so it reads as if a person wrote it from scratch.

Requirements:
- Vary sentence rhythm: mix short sentences with long ones.
- Replace formulaic AI transitions ("Moreover", "Furthermore", "It is important to note",
  "In conclusion", "delve", "tapestry", "landscape") with concrete natural phrasing.
- Cut hedging and filler. Prefer specific verbs over abstract nouns.
- Preserve every fact, number, name, date, and quotation exactly.
- Match the original length closely. The original is {source_words} words, so write
  between {min_words} and {max_words} words.
- Output only the rewritten text. No preamble and no commentary.

TEXT:
{text}`,

  code: `Rewrite the natural-language parts of the code below.

Requirements:
- Rewrite comments, docstrings, and user-facing string literals in different wording.
- Rename local variables and private helpers to semantically equivalent names.
- Do NOT change program behavior, control flow, algorithms, or numeric literals.
- Do NOT rename anything in the public API: exported functions, classes, methods,
  CLI flags, config keys, or anything referenced from outside this file.
- Keep imports, formatting conventions, and language idioms intact.
- Output only the rewritten code, no fences and no commentary.

CODE:
{text}`,

  backtranslate_forward: `Translate the text below into {pivot}.

Translate faithfully and completely. Preserve every fact, number, name, date, and
quotation. Output only the translation.

TEXT:
{text}`,

  backtranslate_back: `Translate the {pivot} text below into {source_language}.

Translate faithfully and completely into natural, idiomatic {source_language}. Preserve
every fact, number, name, date, and quotation. Add nothing that is not in the source.
Output only the translation, with no preamble and no commentary.

TEXT:
{text}`,

  structural_outline: `Read the text below and extract a complete bullet outline of every claim it makes.

One bullet per claim. Keep all facts, numbers, names, and dates. Do not add claims that
are not in the text. Output only the bullet list.

TEXT:
{text}`,

  structural_expand: `Write a complete document from the outline below.

Requirements:
- Cover every bullet. Add nothing that is not in the outline.
- Write in varied, natural prose. Do not mirror the bullet order mechanically if a
  better order exists, but keep the logical structure intact.
- Match this register: {register}.
- Write between {min_words} and {max_words} words.
- Output only the document, with no preamble and no commentary.

OUTLINE:
{text}`,
};

const WORD_RE = /[\p{L}\p{N}_]+/gu;

/** Separator used when rejoining rewritten sections of a long input. */
const SECTION_JOIN = '\n\n';

const fill = (tpl, vars) =>
  tpl.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m));

/** Word count of the source, used to give the model a concrete length target. */
export function countWords(text) {
  return (String(text).match(WORD_RE) || []).length;
}

/** Length variables shared by every prompt that should match the source. */
export function lengthVars(text, tolerance = 0.1) {
  const n = countWords(text);
  return {
    source_words: n,
    min_words: Math.max(1, Math.round(n * (1 - tolerance))),
    max_words: Math.max(1, Math.round(n * (1 + tolerance))),
    target_words: n,
  };
}

export function buildPrompt(text, strength, { pivot = 'French' } = {}) {
  const key =
    strength === 'backtranslate'
      ? 'backtranslate_forward'
      : strength === 'structural'
        ? 'structural_outline'
        : strength;
  const tpl = PROMPTS[key];
  if (!tpl) throw new Error(`Unknown strength: ${strength}`);
  return fill(tpl, { text, pivot, ...lengthVars(text) });
}

/* ── divergence scoring (mirrors rewrite.py) ─────────────────────────────── */

function bigrams(text) {
  const words = (text.toLowerCase().match(WORD_RE) || []);
  const set = new Set();
  for (let i = 0; i + 1 < words.length; i++) set.add(words[i] + ' ' + words[i + 1]);
  return set;
}

/** 1 - Jaccard similarity over word bigrams. Higher means more diverged. */
export function divergence(a, b) {
  const A = bigrams(a);
  const B = bigrams(b);
  if (!A.size && !B.size) return 0;
  let inter = 0;
  for (const g of A) if (B.has(g)) inter++;
  const union = A.size + B.size - inter;
  if (!union) return 0;
  return 1 - inter / union;
}

/* ── output length control ───────────────────────────────────────────────── */

/** Rough token estimate. English averages a little under 4 chars per token. */
export function estimateTokens(text) {
  return Math.ceil(String(text).length / 3.6);
}

/**
 * How many new tokens a rewrite of `source` should be allowed to produce.
 *
 * A fixed cap is wrong in both directions: it lets a short input balloon and
 * truncates a long one mid-sentence. The budget tracks the source, with slack
 * so a sentence is never cut off, and a floor so very short inputs still have
 * room to finish.
 */
export function tokenBudget(source, { slack = 1.35, floor = 96, ceiling = 3072 } = {}) {
  const want = Math.ceil(estimateTokens(source) * slack) + 32;
  return Math.min(ceiling, Math.max(floor, want));
}

const PREAMBLE_RE =
  /^(?:sure|certainly|of course|absolutely|okay|ok)?[,!.]?\s*(?:here\b[^\n:]{0,80}|rewritten(?: text| version)?|rewrite|output|result|paraphrase)\s*[:\-]\s*\n+/i;

const ROLE_MARKER_RE = /<\|[^|>]*\|>/g;

/**
 * Clean up what a small model actually returns.
 *
 * Sub-1B models routinely add "Sure! Here's the rewritten text:", wrap the
 * answer in a code fence, or leak chat-template role markers. All of that
 * inflates the length and is not part of the rewrite.
 */
export function tidyModelOutput(raw, { prompt = '', unwrapQuotes = true } = {}) {
  let t = String(raw ?? '').trim();
  if (!t) return '';

  const needle = String(prompt).trim();
  if (needle && t.startsWith(needle)) t = t.slice(needle.length).trim();

  t = t.replace(ROLE_MARKER_RE, '').trim();
  t = t.replace(PREAMBLE_RE, '').trim();

  // Unwrap a fence only when the whole output is one fenced block.
  const fence = t.match(/^```[\w-]*\s*\n([\s\S]*?)\n?```$/);
  if (fence) t = fence[1].trim();

  // Unwrap surrounding quotes only when they clearly bracket the whole answer.
  if (unwrapQuotes && t.length > 40) {
    const pairs = [['"', '"'], ['“', '”'], ["'", "'"]];
    for (const [open, close] of pairs) {
      if (t.startsWith(open) && t.endsWith(close) && !t.slice(1, -1).includes(open)) {
        t = t.slice(1, -1).trim();
        break;
      }
    }
  }
  return t;
}

/* ── chunking, so any length of input is accepted ────────────────────────── */

//: Titles and abbreviations whose trailing period does not end a sentence.
const ABBREVIATIONS = [
  'Mr', 'Mrs', 'Ms', 'Dr', 'Prof', 'Sr', 'Jr', 'St', 'Rev', 'Hon',
  'vs', 'etc', 'e.g', 'i.e', 'cf', 'al', 'Inc', 'Ltd', 'Co', 'Corp',
  'Fig', 'No', 'Vol', 'pp', 'approx', 'Jan', 'Feb', 'Mar', 'Apr',
  'Jun', 'Jul', 'Aug', 'Sept', 'Sep', 'Oct', 'Nov', 'Dec',
];

// Matches an abbreviation sitting at the very end of a fragment.
// String.raw is required: in a plain template literal `\b` is the backspace
// character, not the word-boundary assertion, and the regex silently never
// matches.
const ABBREV_TAIL_RE = new RegExp(
  String.raw`\b(?:${ABBREVIATIONS.join('|').replace(/\./g, String.raw`\.`)})\.$`,
);

const SENTENCE_SPLIT_RE = /(?<=[.!?])[ 	]+(?=["'([]*[A-Z0-9])/;

/**
 * Rough sentence split. Mirrors split_sentences in rewrite.py.
 *
 * Splits first, then rejoins any fragment that ended on an abbreviation, so
 * "Dr. Chen" stays one sentence. Doing it this way avoids masking the text
 * with a sentinel control character, which would be a poor idea in a tool
 * whose job is removing stray invisible characters.
 */
export function splitSentences(text) {
  const out = [];
  for (const part of String(text).split(SENTENCE_SPLIT_RE)) {
    const prev = out[out.length - 1];
    if (prev !== undefined && ABBREV_TAIL_RE.test(prev)) {
      out[out.length - 1] = `${prev} ${part}`;
    } else {
      out.push(part);
    }
  }
  return out.filter((s) => s.trim());
}

/**
 * Split text into chunks the model can handle.
 *
 * Chunking is what makes arbitrary input length work, and it also improves
 * length fidelity: a model matches a target far more closely over a few
 * hundred words than over several thousand. Paragraph boundaries are
 * preferred; an oversized paragraph falls back to sentence boundaries.
 */
export function chunkText(text, maxChars = 3000) {
  const src = String(text);
  if (src.length <= maxChars) return src.trim() ? [src] : [];

  const pieces = [];
  for (const para of src.split(/\n\n+/)) {
    if (para.length <= maxChars) {
      pieces.push(para);
      continue;
    }
    let buf = '';
    for (const sentence of splitSentences(para)) {
      if (buf && buf.length + sentence.length + 1 > maxChars) {
        pieces.push(buf.trim());
        buf = '';
      }
      buf += (buf ? ' ' : '') + sentence;
    }
    if (buf.trim()) pieces.push(buf.trim());
  }

  const chunks = [];
  let current = [];
  let size = 0;
  for (const piece of pieces) {
    if (size + piece.length > maxChars && current.length) {
      chunks.push(current.join('\n\n'));
      current = [];
      size = 0;
    }
    current.push(piece);
    size += piece.length + 2;
  }
  if (current.length) chunks.push(current.join('\n\n'));
  return chunks.filter((c) => c.trim());
}

/* ── length band enforcement ─────────────────────────────────────────────── */

/** The acceptable word range for a rewrite of `source`. */
export function lengthBand(source, tolerance = 0.1) {
  const target = countWords(source);
  return {
    target,
    min: Math.max(1, Math.round(target * (1 - tolerance))),
    max: Math.max(1, Math.round(target * (1 + tolerance))),
    tolerance,
  };
}

export function withinBand(source, candidate, tolerance = 0.1) {
  const band = lengthBand(source, tolerance);
  const n = countWords(candidate);
  return n >= band.min && n <= band.max;
}

/** Corrective instruction prepended to a retry when a attempt missed the band. */
export function lengthNudge(actualWords, band) {
  const direction =
    actualWords > band.max
      ? 'Your previous attempt was too long. Tighten the phrasing. Do not drop any fact.'
      : 'Your previous attempt was too short. Cover every point in the source in full.';
  return (
    `Your previous attempt was ${actualWords} words. The required length is ` +
    `${band.min} to ${band.max} words. ${direction}\n\n`
  );
}

export function scoreCandidate(original, candidate) {
  const notes = [];
  let score = divergence(original, candidate);
  if (original.length) {
    const ratio = candidate.length / original.length;
    if (ratio > 2.0 || ratio < 0.5) {
      score -= 0.15;
      notes.push(`length drift penalty applied (ratio ${ratio.toFixed(2)})`);
    }
  }
  return { score, notes };
}

/* ── transport ───────────────────────────────────────────────────────────── */

export class RewriteError extends Error {}

async function callModel({
  provider, endpoint, key, model, prompt, temperature, signal, maxTokens,
}) {
  const url = endpoint || provider.endpoint;
  if (!url) throw new RewriteError('No endpoint configured for this provider.');

  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new RewriteError(`Not a valid URL: ${url}`);
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
    throw new RewriteError(`Refusing non-http(s) scheme: ${parsed.protocol}`);
  }

  const headers = { 'Content-Type': 'application/json' };
  if (key) headers.Authorization = `Bearer ${key}`;
  if (provider.extraHeaders) Object.assign(headers, provider.extraHeaders());

  let resp;
  try {
    resp = await fetch(url, {
      method: 'POST',
      headers,
      signal,
      // No credentials, no cookies. Only the bearer token the user supplied.
      credentials: 'omit',
      redirect: 'error',
      body: JSON.stringify({
        model,
        messages: [{ role: 'user', content: prompt }],
        temperature,
        // Without a cap a model can keep going well past the source length.
        ...(maxTokens ? { max_tokens: maxTokens } : {}),
      }),
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    // A CORS rejection surfaces here as an opaque TypeError.
    throw new RewriteError(
      `Could not reach ${parsed.host}. If this provider blocks browser ` +
        `requests (CORS), use the local app instead. Original error: ${e.message}`,
    );
  }

  const raw = await resp.text();
  let data = null;
  try { data = JSON.parse(raw); } catch { /* keep raw for the message */ }

  if (!resp.ok) {
    const detail = data?.error?.message || raw.slice(0, 300);
    if (resp.status === 401 || resp.status === 403) {
      throw new RewriteError(`Key rejected by ${provider.label} (HTTP ${resp.status}). ${detail}`);
    }
    if (resp.status === 429) {
      throw new RewriteError(`Rate limited by ${provider.label}. ${detail}`);
    }
    throw new RewriteError(`HTTP ${resp.status} from ${provider.label}: ${detail}`);
  }

  const content = data?.choices?.[0]?.message?.content;
  if (!content) {
    throw new RewriteError(`${provider.label} returned no content: ${raw.slice(0, 200)}`);
  }
  return content.trim();
}

/* ── pipeline ────────────────────────────────────────────────────────────── */

async function rewriteChunk(text, opts) {
  const {
    provider,
    endpoint = null,
    key = '',
    model,
    strength = 'paraphrase',
    temperature = 0.9,
    candidates = 1,
    pivot = 'French',
    sourceLanguage = 'English',
    tolerance = 0.1,
    lengthRetries = 2,
    onProgress = () => {},
    signal,
    // Injected by the in-browser model path; bypasses fetch entirely.
    localGenerate = null,
  } = opts;

  const band = lengthBand(text, tolerance);

  // Budget the output against the source, so a rewrite stays close in length.
  const maxTokens = tokenBudget(text);

  const rawGenerate = localGenerate
    ? (prompt) => localGenerate(prompt, { temperature, signal, maxNewTokens: maxTokens })
    : (prompt) =>
        callModel({
          provider, endpoint, key, model, prompt, temperature, signal, maxTokens,
        });

  // Small models add preambles, fences and role markers. Strip them before
  // anything downstream measures the length or scores the divergence.
  const generate = async (prompt) =>
    tidyModelOutput(await rawGenerate(prompt), {
      prompt,
      unwrapQuotes: strength !== 'code',
    });

  const prompt = buildPrompt(text, strength, { pivot });

  const onePass = async (nudge = '') => {
    if (strength === 'backtranslate') {
      onProgress(`translating into ${pivot}`);
      const mid = await generate(fill(PROMPTS.backtranslate_forward, { text, pivot }));
      onProgress(`translating back into ${sourceLanguage}`);
      return generate(
        nudge + fill(PROMPTS.backtranslate_back, {
          text: mid, pivot, source_language: sourceLanguage,
          ...lengthVars(text, tolerance),
        }),
      );
    }
    if (strength === 'structural') {
      onProgress('extracting an outline');
      const outline = await generate(fill(PROMPTS.structural_outline, { text }));
      onProgress('rewriting from the outline');
      return generate(
        nudge + fill(PROMPTS.structural_expand, {
          text: outline,
          register: 'the same register as the original',
          ...lengthVars(text, tolerance),
        }),
      );
    }
    return generate(nudge + prompt);
  };

  // Ask again when an attempt misses the band, telling the model what it
  // actually produced. The prompt alone is a request; this is the part that
  // makes the length hold. The closest attempt is kept if none land in band.
  const attempt = async (label) => {
    let best = null;
    for (let i = 0; i <= lengthRetries; i++) {
      const nudge = i === 0 || !best ? '' : lengthNudge(best.words, band);
      if (i > 0) onProgress(`${label}: adjusting length, attempt ${i + 1}`);
      const out = await onePass(nudge);
      const words = countWords(out);
      const cand = { text: out, words, inBand: words >= band.min && words <= band.max };
      if (cand.inBand) return cand;
      if (!best || Math.abs(words - band.target) < Math.abs(best.words - band.target)) {
        best = cand;
      }
    }
    return best ?? { text: '', words: 0, inBand: false };
  };

  const scored = [];
  for (let i = 0; i < candidates; i++) {
    const label = `candidate ${i + 1} of ${candidates}`;
    onProgress(label);
    const cand = await attempt(label);
    const { score, notes } = scoreCandidate(text, cand.text);
    // An in-band candidate always beats an out-of-band one, however diverged.
    scored.push({
      text: cand.text,
      words: cand.words,
      inBand: cand.inBand,
      score: score + (cand.inBand ? 1 : 0),
      divergence: divergence(text, cand.text),
      notes,
    });
  }
  scored.sort((a, b) => b.score - a.score);
  const winner = scored[0];

  return {
    text: winner.text,
    words: winner.words,
    inBand: winner.inBand,
    candidates: scored,
    prompt,
    band,
  };
}

/**
 * Rewrite `text`, keeping the result close to the source length.
 *
 * Long input is split into chunks and each chunk is rewritten against its own
 * word target, so any length of input is accepted and the length tracks the
 * source section by section instead of drifting across the whole document.
 */
export async function rewrite(text, opts) {
  const {
    strength = 'paraphrase',
    candidates = 1,
    layerAAfter = true,
    tolerance = 0.1,
    chunkChars = null,
    onProgress = () => {},
    localGenerate = null,
  } = opts;

  if (candidates < 1 || candidates > 5) {
    throw new RewriteError('candidates must be between 1 and 5');
  }

  // An in-browser model is slow and has a small effective context, so it gets
  // smaller chunks. A hosted model can take more per call.
  const limit = chunkChars ?? (localGenerate ? 1200 : 4000);

  // The two-step strengths reason over the whole passage, so chunking them
  // would change what they do. They stay whole.
  const chunked = strength === 'backtranslate' || strength === 'structural'
    ? [text]
    : chunkText(text, limit);
  const pieces = chunked.length ? chunked : [text];

  const results = [];
  for (let i = 0; i < pieces.length; i++) {
    if (pieces.length > 1) onProgress(`section ${i + 1} of ${pieces.length}`);
    results.push(
      await rewriteChunk(pieces[i], {
        ...opts,
        onProgress: (m) =>
          onProgress(pieces.length > 1 ? `section ${i + 1}/${pieces.length}: ${m}` : m),
      }),
    );
  }

  let best = results.map((r) => r.text).join(SECTION_JOIN);
  let layerAReport = null;
  const notes = [];

  if (layerAAfter) {
    const cleaned = layerAClean(best);
    best = cleaned.text;
    layerAReport = cleaned;
    if (cleaned.totalRemoved || cleaned.totalReplaced) {
      notes.push(
        `The rewrite model introduced ${cleaned.totalRemoved} invisible ` +
          `character(s) of its own. Layer A removed them.`,
      );
    }
  }

  const sourceWords = countWords(text);
  const outputWords = countWords(best);
  const ratio = sourceWords ? outputWords / sourceWords : 1;
  const band = lengthBand(text, tolerance);
  const inBand = outputWords >= band.min && outputWords <= band.max;

  if (pieces.length > 1) {
    notes.push(
      `Input was split into ${pieces.length} sections, each rewritten against ` +
        `its own length target.`,
    );
  }

  if (inBand) {
    notes.push(
      `Length: ${outputWords} words against ${sourceWords} in the source, ` +
        `within the ${Math.round(tolerance * 100)}% target.`,
    );
  } else {
    notes.push(
      `Length: ${outputWords} words against ${sourceWords} in the source ` +
        `(${ratio >= 1 ? '+' : ''}${Math.round((ratio - 1) * 100)}%), outside the ` +
        `${Math.round(tolerance * 100)}% target. The model was asked again and ` +
        `this was its closest attempt. A larger model holds a length target ` +
        `much better than a 0.5B one.`,
    );
  }

  notes.push(
    'Cross-vendor hygiene: rewrite with a model from a different vendor than ' +
      'the one that wrote the text. Rewriting with the origin model can ' +
      're-apply the same watermark key.',
  );

  const finalDivergence = divergence(text, best);
  if (finalDivergence < 0.5) {
    notes.push(
      `Divergence is only ${(finalDivergence * 100).toFixed(0)}%. The wording ` +
        `barely changed, so some of the original token pattern may survive. ` +
        `Try a higher temperature, more candidates, or a stronger setting.`,
    );
  }

  return {
    layer: 'B',
    original: text,
    text: best,
    strength,
    model: opts.model,
    provider: opts.provider?.label ?? 'in-browser',
    inputLength: text.length,
    outputLength: best.length,
    inputWords: sourceWords,
    outputWords,
    lengthRatio: ratio,
    withinTolerance: inBand,
    tolerance,
    sections: pieces.length,
    divergence: finalDivergence,
    candidates: results[0]?.candidates ?? [],
    prompt: results[0]?.prompt ?? '',
    layerAAfter: layerAReport,
    notes,
  };
}
