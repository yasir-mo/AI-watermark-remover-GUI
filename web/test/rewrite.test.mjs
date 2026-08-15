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
    localGenerate: async () => outputs[i++],
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
      provider: stubProvider, strength,
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

test('a missing endpoint is reported, not silently ignored', async () => {
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
