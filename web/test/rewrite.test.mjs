/* Layer B browser client: prompts, scoring, transport guards, pipeline. */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  RewriteError, STRENGTHS, buildPrompt, divergence, rewrite, scoreCandidate,
} from '../src/rewrite.js';
import { PROVIDERS, browserProviders, blockedProviders, byId } from '../src/providers.js';
import { EXAMPLES, watermarkedSample } from '../src/examples.js';
import { scan, clean } from '../src/layer-a.js';

const SAMPLE =
  'The committee released its findings on 14 March 2023. ' +
  'Revenue grew 12% to $4.2 million. Dr. Alice Chen led the review.';

/* ── prompts ─────────────────────────────────────────────────────────────── */

test('every strength builds a prompt containing the text', () => {
  for (const s of STRENGTHS) {
    const p = buildPrompt(SAMPLE, s.id);
    assert.ok(p.includes(SAMPLE), `${s.id} dropped the text`);
    assert.ok(p.length > SAMPLE.length);
  }
});

test('unknown strength is rejected', () => {
  assert.throws(() => buildPrompt(SAMPLE, 'nonsense'), /Unknown strength/);
});

test('prose prompts demand fact preservation', () => {
  for (const id of ['paraphrase', 'humanize']) {
    const p = buildPrompt(SAMPLE, id).toLowerCase();
    assert.ok(p.includes('preserve'), `${id} missing preserve`);
    assert.ok(p.includes('number'), `${id} missing number`);
  }
});

test('code prompt protects the public API', () => {
  const p = buildPrompt('def f(): pass', 'code').toLowerCase();
  assert.ok(p.includes('public api'));
  assert.ok(p.includes('behavior'));
});

test('backtranslate honours the pivot language', () => {
  assert.ok(buildPrompt(SAMPLE, 'backtranslate', { pivot: 'German' }).includes('German'));
});

test('prompts leave no unfilled placeholders', () => {
  for (const s of STRENGTHS) {
    const p = buildPrompt(SAMPLE, s.id);
    assert.ok(!/\{(text|pivot|register|target_words|source_language)\}/.test(p),
      `${s.id} has an unfilled placeholder`);
  }
});

/* ── scoring ─────────────────────────────────────────────────────────────── */

test('identical text has zero divergence', () => {
  assert.equal(divergence(SAMPLE, SAMPLE), 0);
});

test('unrelated text diverges fully', () => {
  assert.equal(divergence('alpha beta gamma', 'delta epsilon zeta'), 1);
});

test('partial overlap sits between', () => {
  const d = divergence('the cat sat on the mat', 'the cat stood on the mat');
  assert.ok(d > 0 && d < 1, `got ${d}`);
});

test('empty inputs do not crash', () => {
  assert.equal(divergence('', ''), 0);
  assert.equal(divergence('', 'words here'), 1);
});

test('length drift is penalised', () => {
  const original = 'word '.repeat(40);
  const bloated = 'different phrasing entirely '.repeat(40);
  const { score, notes } = scoreCandidate(original, bloated);
  assert.ok(notes.some((n) => n.includes('length drift')));
  assert.ok(score < divergence(original, bloated));
});

test('normal length carries no penalty', () => {
  const { notes } = scoreCandidate(SAMPLE, SAMPLE.replace('grew', 'rose'));
  assert.deepEqual(notes, []);
});

test('divergence matches the Python definition on a known pair', () => {
  // 1 - |A n B| / |A u B| over word bigrams.
  const a = 'one two three';           // {one two, two three}
  const b = 'one two four';            // {one two, two four}
  assert.equal(divergence(a, b), 1 - 1 / 3);
});

/* ── pipeline, with a stubbed model ──────────────────────────────────────── */

const stubProvider = { id: 'stub', label: 'Stub', endpoint: 'https://example.test/v1' };

test('candidate count is validated', async () => {
  for (const n of [0, 6, -1]) {
    await assert.rejects(
      rewrite(SAMPLE, {
        provider: stubProvider, candidates: n,
        localGenerate: async () => 'x',
      }),
      /between 1 and 5/,
    );
  }
});

test('picks the most diverged candidate', async () => {
  const outputs = [
    'The committee released its findings on 14 March 2023.', // near-identical
    'On 14 March 2023 a panel published what it had found.',  // diverged
  ];
  let i = 0;
  const r = await rewrite(SAMPLE, {
    provider: stubProvider,
    candidates: 2,
    layerAAfter: false,
    lengthRetries: 0, // isolate divergence selection from length retries
    localGenerate: async () => outputs[Math.min(i++, outputs.length - 1)],
  });
  assert.ok(r.text.startsWith('On 14 March 2023'));
  assert.equal(r.candidates.length, 2);
  const scores = r.candidates.map((c) => c.score);
  assert.deepEqual(scores, [...scores].sort((a, b) => b - a), 'not sorted best-first');
});

test('Layer A runs over the model output', async () => {
  const dirty = 'The panel​ published⁠ its findings.';
  const r = await rewrite(SAMPLE, {
    provider: stubProvider,
    localGenerate: async () => dirty,
  });
  assert.ok(!r.text.includes('​'), 'zero-width space survived');
  assert.ok(!r.text.includes('⁠'), 'word joiner survived');
  assert.equal(r.layerAAfter.totalRemoved, 2);
  assert.ok(r.notes.some((n) => n.includes('invisible')));
});

test('Layer A pass can be disabled', async () => {
  const dirty = 'The panel​ published.';
  const r = await rewrite(SAMPLE, {
    provider: stubProvider, layerAAfter: false,
    localGenerate: async () => dirty,
  });
  assert.ok(r.text.includes('​'));
});

test('always warns about cross-vendor rewriting', async () => {
  const r = await rewrite(SAMPLE, {
    provider: stubProvider, localGenerate: async () => 'Something quite different indeed.',
  });
  assert.ok(r.notes.some((n) => n.includes('different vendor')));
});

test('warns when divergence is too low to be meaningful', async () => {
  const r = await rewrite(SAMPLE, {
    provider: stubProvider, localGenerate: async () => SAMPLE, // no change at all
  });
  assert.ok(r.notes.some((n) => n.includes('barely changed')), r.notes.join(' | '));
});

test('two-step strengths make two calls', async () => {
  for (const strength of ['backtranslate', 'structural']) {
    let calls = 0;
    await rewrite(SAMPLE, {
      provider: stubProvider, strength, lengthRetries: 0,
      localGenerate: async () => { calls++; return 'intermediate output text'; },
    });
    assert.equal(calls, 2, `${strength} should make two calls`);
  }
});

test('non-http endpoints are refused', async () => {
  await assert.rejects(
    rewrite(SAMPLE, {
      provider: { id: 'x', label: 'X', endpoint: 'file:///etc/passwd' },
      key: 'k', model: 'm',
    }),
    (e) => e instanceof RewriteError && /non-http/.test(e.message),
  );
});

test('a missing endpoint raises instead of being ignored', async () => {
  await assert.rejects(
    rewrite(SAMPLE, { provider: { id: 'x', label: 'X', endpoint: null }, key: 'k' }),
    /No endpoint configured/,
  );
});

/* ── provider registry ───────────────────────────────────────────────────── */

test('registry entries are well formed', () => {
  const ids = new Set();
  for (const p of PROVIDERS) {
    assert.ok(p.id && p.label, 'provider needs id and label');
    assert.ok(!ids.has(p.id), `duplicate provider id: ${p.id}`);
    ids.add(p.id);
    assert.equal(typeof p.browserDirect, 'boolean', `${p.id} must declare browserDirect`);
    if (p.endpoint) {
      assert.doesNotThrow(() => new URL(p.endpoint), `${p.id} endpoint is not a URL`);
      assert.ok(p.endpoint.startsWith('https://'), `${p.id} endpoint must be https`);
    }
    if (!p.browserDirect) {
      assert.ok(p.freeNote?.includes('local app'),
        `${p.id} should tell the user where it does work`);
    }
  }
});

test('the CORS split matches what was measured', () => {
  const direct = browserProviders().map((p) => p.id);
  const blocked = blockedProviders().map((p) => p.id);
  for (const id of ['openrouter', 'google', 'mistral', 'deepseek', 'huggingface']) {
    assert.ok(direct.includes(id), `${id} should be browser-direct`);
  }
  for (const id of ['groq', 'cerebras', 'together']) {
    assert.ok(blocked.includes(id), `${id} should be marked CORS-blocked`);
  }
});

test('OpenRouter free filter keeps only free models', () => {
  const or = byId('openrouter');
  const picked = or.filterModels([
    { id: 'a/b:free', pricing: { prompt: '0' } },
    { id: 'c/d', pricing: { prompt: '0.0000015' } },
    { id: 'e/f', pricing: { prompt: '0' } },
  ]);
  assert.deepEqual(picked, ['a/b:free', 'e/f']);
});

/* ── examples ────────────────────────────────────────────────────────────── */

test('every example actually diverges from its source', () => {
  for (const ex of EXAMPLES) {
    const d = divergence(ex.before, ex.after);
    assert.ok(d > 0.5, `${ex.id} only diverges ${(d * 100).toFixed(0)}%`);
  }
});

test('examples name a real strength', () => {
  const ids = new Set(STRENGTHS.map((s) => s.id));
  for (const ex of EXAMPLES) assert.ok(ids.has(ex.strength), `${ex.id}: ${ex.strength}`);
});

test('examples preserve their numbers and dates', () => {
  // Strip sentence punctuation that clings to a figure, so "2023." and
  // "12%," compare equal to "2023" and "12%".
  const nums = (s) =>
    (s.match(/\d[\d.,%$]*/g) || [])
      .map((n) => n.replace(/[.,]+$/, ''))
      .sort();
  for (const ex of EXAMPLES) {
    if (ex.id === 'code') continue; // no figures to carry
    assert.deepEqual(nums(ex.after), nums(ex.before),
      `${ex.id} lost or invented a figure`);
  }
});

test('the watermarked sample really is watermarked', () => {
  const s = scan(watermarkedSample());
  assert.ok(s.totalRemovable >= 6, `only ${s.totalRemovable} removable`);
  assert.ok(s.watermarkSuspected);
  const kinds = Object.keys(s.byKind);
  for (const k of ['zwj_family', 'tag_chars', 'space']) {
    assert.ok(kinds.includes(k), `sample should demonstrate ${k}`);
  }
});

test('the sample keeps its legitimate emoji joiner', () => {
  const c = clean(watermarkedSample());
  assert.equal(c.keptLoadBearing.emoji_sequence, 1);
  assert.ok(c.text.includes('\u{1F469}‍\u{1F4BC}'));
});

/* ── output length control (regression: 900 chars in, 4000 out) ──────────── */

import { estimateTokens, tokenBudget, tidyModelOutput, countWords, lengthVars }
  from '../src/rewrite.js';
import { extractAssistantText } from '../src/local-model.js';

test('token budget scales with the source instead of a fixed cap', () => {
  const short = 'word '.repeat(50);   // ~250 chars
  const long = 'word '.repeat(2000);  // ~10k chars
  assert.ok(tokenBudget(short) < tokenBudget(long));
  // A 900-character input must not be allowed 512 tokens of headroom.
  const nineHundred = 'a'.repeat(900);
  assert.ok(tokenBudget(nineHundred) < 512,
    `budget for 900 chars was ${tokenBudget(nineHundred)}`);
  assert.ok(tokenBudget(nineHundred) > estimateTokens(nineHundred),
    'budget must leave room to finish a sentence');
});

test('token budget respects floor and ceiling', () => {
  assert.equal(tokenBudget(''), 96);
  assert.ok(tokenBudget('x'.repeat(500000)) <= 3072);
});

test('word count targets bracket the source by 10%', () => {
  const v = lengthVars('one two three four five six seven eight nine ten');
  assert.equal(v.source_words, 10);
  assert.equal(v.min_words, 9);
  assert.equal(v.max_words, 11);
});

test('prompts state a concrete word range', () => {
  const text = 'word '.repeat(100);
  for (const id of ['paraphrase', 'humanize']) {
    const p = buildPrompt(text, id);
    assert.ok(/between 90 and 110 words/.test(p), `${id} lacks a word range`);
  }
});

test('countWords matches the divergence tokenizer', () => {
  assert.equal(countWords('the committee met on Tuesday'), 5);
  // Punctuation inside figures splits too: "$4.2" counts as 4 and 2.
  assert.equal(countWords('revenue rose 12% to $4.2 million'), 7);
  // The tokenizer splits on apostrophes, so contractions count as two. That is
  // close enough for a length target and keeps it consistent with divergence().
  assert.equal(countWords("it's"), 2);
});

/* extractAssistantText: the actual 4000-character bug */

test('assistant text is read from a chat-array result', () => {
  const out = [{ generated_text: [
    { role: 'user', content: 'PROMPT' },
    { role: 'assistant', content: 'the rewrite' },
  ] }];
  assert.equal(extractAssistantText(out, 'PROMPT'), 'the rewrite');
});

test('a string result never returns the prompt back', () => {
  // Regression. `.at(-1)` on a string yields a character, `.content` is
  // undefined, and the old fallback returned the whole string, prompt and all.
  const prompt = 'Rewrite the text below.\n\nTEXT:\n' + 'source '.repeat(120);
  const out = [{ generated_text: prompt + '\nTHE ACTUAL REWRITE' }];
  const got = extractAssistantText(out, prompt);
  assert.equal(got, 'THE ACTUAL REWRITE');
  assert.ok(!got.includes('Rewrite the text below'), 'prompt leaked into output');
  assert.ok(got.length < prompt.length / 5);
});

test('extractAssistantText tolerates junk', () => {
  assert.equal(extractAssistantText(undefined, 'p'), '');
  assert.equal(extractAssistantText([], 'p'), '');
  assert.equal(extractAssistantText([{}], 'p'), '');
});

/* tidyModelOutput */

test('preambles from small models are removed', () => {
  for (const pre of [
    "Sure! Here's the rewritten text:\n",
    'Here is the rewrite:\n',
    'Rewritten text:\n',
    'Certainly, here you are:\n',
  ]) {
    assert.equal(tidyModelOutput(pre + 'The actual content.'), 'The actual content.',
      `failed to strip: ${pre.trim()}`);
  }
});

test('a whole-output code fence is unwrapped', () => {
  assert.equal(tidyModelOutput('```\nplain content\n```'), 'plain content');
  assert.equal(tidyModelOutput('```python\ndef f():\n    pass\n```'),
    'def f():\n    pass');
});

test('chat role markers are stripped', () => {
  assert.equal(tidyModelOutput('<|im_start|>assistant\nHello<|im_end|>').trim(),
    'assistant\nHello');
});

test('tidying does not damage ordinary prose', () => {
  const prose = 'The committee met on Tuesday. Revenue rose 12% to $4.2 million.';
  assert.equal(tidyModelOutput(prose), prose);
});

test('code strength keeps its quotes', () => {
  const code = '"use strict";\nconst x = 1;\nconsole.log("hello world here");';
  assert.equal(tidyModelOutput(code, { unwrapQuotes: false }), code);
});

test('the pipeline tidies and length-limits what a model returns', async () => {
  const source = 'The committee met on Tuesday and approved the budget. '.repeat(6);
  let sawMaxTokens = null;
  const r = await rewrite(source, {
    provider: stubProvider,
    layerAAfter: false,
    localGenerate: async (prompt, opts) => {
      sawMaxTokens = opts.maxNewTokens;
      return "Sure! Here's the rewritten text:\n```\nA board convened Tuesday and signed off the spending plan.\n```";
    },
  });
  assert.equal(r.text, 'A board convened Tuesday and signed off the spending plan.');
  assert.ok(sawMaxTokens > 0 && sawMaxTokens < 3072, `maxNewTokens was ${sawMaxTokens}`);
});

/* ── length band and arbitrary input size ────────────────────────────────── */

import { chunkText, splitSentences, lengthBand, withinBand, lengthNudge }
  from '../src/rewrite.js';

const SENTENCE = 'The committee reviewed the quarterly figures and approved it. ';

test('short and long input are both accepted', async () => {
  for (const source of ['Hi there.', SENTENCE.repeat(400)]) {
    const r = await rewrite(source, {
      provider: stubProvider, layerAAfter: false, lengthRetries: 0,
      localGenerate: async (p) => {
        // Echo back a same-length paraphrase of whatever chunk was sent.
        const body = p.slice(p.lastIndexOf('TEXT:') + 5).trim();
        return body.replace(/committee/g, 'panel').replace(/approved/g, 'signed off');
      },
    });
    assert.ok(r.text.length > 0, 'empty output');
    assert.ok(r.outputWords > 0);
  }
});

test('long input is split into sections', async () => {
  const source = SENTENCE.repeat(400); // ~24k characters
  let calls = 0;
  const r = await rewrite(source, {
    provider: stubProvider, layerAAfter: false, lengthRetries: 0,
    localGenerate: async (p) => { calls++; return p.slice(p.lastIndexOf('TEXT:') + 5).trim(); },
  });
  assert.ok(r.sections > 1, `expected several sections, got ${r.sections}`);
  assert.equal(calls, r.sections);
  assert.ok(r.notes.some((n) => n.includes('split into')));
});

test('a model that overruns is asked again with a correction', async () => {
  const source = SENTENCE.repeat(4);
  const seen = [];
  let call = 0;
  const r = await rewrite(source, {
    provider: stubProvider, layerAAfter: false, lengthRetries: 2,
    localGenerate: async (p) => {
      seen.push(p);
      call++;
      // First attempt runs far too long; the retry behaves.
      return call === 1
        ? 'padding words here '.repeat(120)
        : source.replace(/committee/g, 'panel');
    },
  });
  assert.ok(call > 1, 'no retry was attempted');
  assert.ok(seen[1].startsWith('Your previous attempt was'),
    'retry prompt lacks the length correction');
  assert.ok(r.withinTolerance, `final result out of band: ${r.outputWords}/${r.inputWords}`);
});

test('the result reports the achieved length honestly', async () => {
  const source = SENTENCE.repeat(4);
  const r = await rewrite(source, {
    provider: stubProvider, layerAAfter: false, lengthRetries: 0,
    localGenerate: async () => 'far too short',
  });
  assert.equal(r.withinTolerance, false);
  assert.ok(r.lengthRatio < 0.5);
  assert.ok(r.notes.some((n) => n.includes('outside the')),
    'must say the target was missed: ' + r.notes.join(' | '));
});

test('lengthBand brackets by the tolerance', () => {
  const band = lengthBand('one two three four five six seven eight nine ten', 0.1);
  assert.deepEqual([band.min, band.target, band.max], [9, 10, 11]);
  assert.ok(withinBand('a b c d e f g h i j', 'a b c d e f g h i j'));
  assert.ok(!withinBand('a b c d e f g h i j', 'a b c'));
});

test('lengthNudge tells the model which way to move', () => {
  const band = lengthBand('one two three four five', 0.1);
  assert.ok(/too long/.test(lengthNudge(99, band)));
  assert.ok(/too short/.test(lengthNudge(1, band)));
  assert.ok(lengthNudge(99, band).includes(`${band.min} to ${band.max} words`));
  assert.ok(lengthNudge(99, band).includes('99 words'), 'must state what it produced');
});

test('chunkText keeps every word', () => {
  const source = SENTENCE.repeat(200);
  const chunks = chunkText(source, 1200);
  assert.ok(chunks.length > 1);
  const rejoined = chunks.join(' ').replace(/\s+/g, ' ').trim();
  assert.equal(countWords(rejoined), countWords(source));
  for (const c of chunks) assert.ok(c.length <= 1200 * 1.5, `chunk too big: ${c.length}`);
});

test('chunkText respects paragraph boundaries', () => {
  const source = ['para one here.', 'para two here.', 'para three here.'].join('\n\n');
  assert.deepEqual(chunkText(source, 10000), [source]);
});

test('chunkText splits a single oversized paragraph on sentences', () => {
  const one = SENTENCE.repeat(60); // one paragraph, no blank lines
  const chunks = chunkText(one, 600);
  assert.ok(chunks.length > 1);
  for (const c of chunks) assert.ok(c.trim().endsWith('.'), `chunk cut mid-sentence: ${c.slice(-40)}`);
});

test('splitSentences keeps abbreviations intact', () => {
  const s = splitSentences('Dr. Chen met the board. See Fig. 3 for detail. Done.');
  assert.equal(s.length, 3);
  assert.ok(s[0].includes('Dr. Chen'));
  assert.ok(s[1].includes('Fig. 3'));
});

test('two-step strengths are not chunked', async () => {
  const source = SENTENCE.repeat(400);
  const r = await rewrite(source, {
    provider: stubProvider, strength: 'structural', layerAAfter: false, lengthRetries: 0,
    localGenerate: async () => 'outline based rewrite',
  });
  assert.equal(r.sections, 1, 'structural must see the whole passage');
});
