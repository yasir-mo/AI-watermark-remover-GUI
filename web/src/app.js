/* wmtk web app. No framework, no build step, no backend.
 *
 * Security note that governs this whole file: the page holds the visitor's API
 * key, so script injection here means key theft. Nothing untrusted is ever
 * assigned to innerHTML — every dynamic string goes through textContent.
 */

import { clean, scan, diffOps } from './layer-a.js';
import { PROVIDERS, browserProviders, byId, listModels } from './providers.js';
import { STRENGTHS, buildPrompt, divergence, rewrite, RewriteError } from './rewrite.js';
import { EXAMPLES, SAMPLE_NOTE, watermarkedSample } from './examples.js';
import * as keystore from './keystore.js';
import { MODELS, loadLocalModel, webgpuUsable } from './local-model.js';

const $ = (id) => document.getElementById(id);

/* ── small helpers ───────────────────────────────────────────────────────── */

let toastTimer;
function toast(msg, isError = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('err', isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 8000 : 3200);
}

function busy(btn, on, label) {
  if (on) {
    btn.dataset.prev = btn.textContent;
    btn.textContent = label || 'Working…';
    btn.disabled = true;
  } else {
    if (btn.dataset.prev) btn.textContent = btn.dataset.prev;
    btn.disabled = false;
  }
}

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

/* Short labels for the reveal chips. */
const SHORT = {
  0x00ad: 'SHY', 0x034f: 'CGJ', 0x061c: 'ALM',
  0x200b: 'ZWSP', 0x200c: 'ZWNJ', 0x200d: 'ZWJ', 0x200e: 'LRM', 0x200f: 'RLM',
  0x202a: 'LRE', 0x202b: 'RLE', 0x202c: 'PDF', 0x202d: 'LRO', 0x202e: 'RLO',
  0x2060: 'WJ', 0x2061: 'FA', 0x2062: 'IT', 0x2063: 'ISEP', 0x2064: 'IPLUS',
  0x2066: 'LRI', 0x2067: 'RLI', 0x2068: 'FSI', 0x2069: 'PDI',
  0xfeff: 'BOM', 0x00a0: 'NBSP', 0x202f: 'NNBSP', 0x205f: 'MMSP',
  0x3000: 'IDSP', 0x1680: 'OGSP', 0xfff9: 'IAA', 0xfffa: 'IAS', 0xfffb: 'IAT',
};

function shortLabel(cp, name) {
  if (SHORT[cp]) return SHORT[cp];
  if (cp >= 0xfe00 && cp <= 0xfe0f) return 'VS' + (cp - 0xfe00 + 1);
  if (cp >= 0xe0100 && cp <= 0xe01ef) return 'VS' + (cp - 0xe0100 + 17);
  if (cp >= 0xe0020 && cp <= 0xe007f) return 'TAG';
  if (cp === 0xe0001) return 'LTAG';
  if (cp >= 0x2000 && cp <= 0x200a) return 'SP' + (cp - 0x2000);
  if (name?.startsWith('<')) return 'PUA';
  const initials = (name || '').split(/[\s-]+/).map((w) => w[0]).join('');
  return initials.length > 1 ? initials.slice(0, 5) : (name || '?').slice(0, 4);
}

const KIND_VAR = {
  zwj_family: 'zwj', tag_chars: 'tag', variation_selector: 'vs',
  private_use: 'pua', other_cf: 'other', confusable: 'conf',
  bidi: 'bidi', space: 'space', strip: 'strip',
};

/* ── tabs ────────────────────────────────────────────────────────────────── */

const TABS = ['strip', 'rewrite', 'examples', 'about'];

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => {
      const on = t === tab;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', String(on));
    });
    for (const name of TABS) $(`tab-${name}`).hidden = name !== tab.dataset.tab;
    location.hash = tab.dataset.tab;
  });
});

function openTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (tab) tab.click();
}

/* ══════════════════════════════════════════════════════ STRIP (Layer A) ══ */

const src = $('src');
const out = $('out');

const stripOpts = () => ({
  aggressive: $('opt-aggressive').checked,
  stripEmojiGlue: $('opt-glue').checked,
  normalize: $('opt-nfkc').checked,
});

function runStrip() {
  const text = src.value;
  $('src-count').textContent = `${Array.from(text).length} characters`;

  if (!text) {
    $('reveal-block').hidden = true;
    $('findings-block').hidden = true;
    out.value = '';
    $('out-count').textContent = '—';
    return;
  }

  const o = stripOpts();
  const s = scan(text, o);
  const c = clean(text, o);

  out.value = c.text;
  $('out-count').textContent =
    `${c.outputLength} characters · ${c.totalRemoved} removed · ${c.totalReplaced} replaced`;

  renderFindings(s);
  renderReveal(s, text, o);
}

function renderFindings(s) {
  const tbody = $('findings').querySelector('tbody');
  tbody.replaceChildren();
  $('findings-block').hidden = false;

  $('findings-summary').textContent = s.findings.length
    ? `${s.totalFlagged} flagged, ${s.totalRemovable} removable across ${s.findings.length} distinct codepoint(s).`
    : 'Nothing hidden in this text.';

  for (const f of s.findings) {
    const tr = el('tr');

    const cp = el('td');
    cp.appendChild(el('span', 'cp', f.codepoint));
    tr.appendChild(cp);

    tr.appendChild(el('td', 'uname', f.name));

    const kd = el('td');
    const tag = el('span', 'kindtag', f.kind);
    tag.dataset.kind = f.kind;
    kd.appendChild(tag);
    if (f.confidence !== 'probable') kd.appendChild(el('div', 'offsets', f.confidence));
    tr.appendChild(kd);

    tr.appendChild(el('td', 'num', String(f.count)));

    const kp = el('td', 'num');
    if (f.keptCount) {
      kp.appendChild(el('span', 'kept-note', String(f.keptCount)));
      kp.title = 'Preserved: ' + Object.keys(f.keptReasons).join(', ');
    } else {
      kp.textContent = '0';
    }
    tr.appendChild(kp);

    const shown = f.offsets.slice(0, 10).join(', ');
    tr.appendChild(el('td', 'offsets', shown + (f.offsets.length > 10 ? ', …' : '')));

    tbody.appendChild(tr);
  }

  const keptTotal = s.findings.reduce((n, f) => n + f.keptCount, 0);
  const note = $('kept-note');
  note.hidden = keptTotal === 0;
  if (keptTotal) {
    note.textContent =
      `${keptTotal} character(s) were found but deliberately kept: they are ` +
      `load-bearing, meaning emoji sequences or scripts like Persian and ` +
      `Devanagari need them to render correctly. Removing them would corrupt the text.`;
  }
}

function renderReveal(s, text, opts) {
  const block = $('reveal-block');
  const host = $('reveal');
  const ruler = $('ruler');
  host.replaceChildren();
  ruler.replaceChildren();

  if (!s.findings.length) { block.hidden = true; return; }
  block.hidden = false;

  const meta = new Map(s.findings.map((f) => [f.codepointInt, f]));
  const chars = Array.from(text);
  const total = chars.length || 1;
  let plain = '';
  let n = 0;

  const flush = () => {
    if (plain) { host.appendChild(document.createTextNode(plain)); plain = ''; }
  };

  // Which source offsets the cleaner will actually touch. A load-bearing
  // character stays inside an "equal" run, so it never lands in this set and
  // is drawn as a kept chip instead.
  const removedPositions = new Set();
  {
    let pos = 0;
    for (const op of diffOps(text, opts)) {
      if (op.op !== 'equal') removedPositions.add(pos);
      pos += Array.from(op.text).length;
    }
  }

  for (let i = 0; i < chars.length; i++) {
    const cp = chars[i].codePointAt(0);
    const f = meta.get(cp);
    if (!f) { plain += chars[i]; continue; }

    flush();
    const willRemove = removedPositions.has(i);
    const chip = el('span', 'chip' + (willRemove ? '' : ' is-kept'), shortLabel(cp, f.name));
    chip.dataset.kind = f.kind;
    chip.title =
      `${f.codepoint} ${f.name} · ${f.kind} · offset ${i}` +
      (willRemove ? '' : ' · KEPT: needed for rendering');
    if (n < 60) chip.style.setProperty('--d', `${n * 12}ms`);
    host.appendChild(chip);

    if (f.kind === 'space') host.appendChild(document.createTextNode(' '));

    const tick = el('span', 'tick');
    tick.style.left = `${(i / total) * 100}%`;
    tick.style.background = `var(--k-${KIND_VAR[f.kind] || 'other'})`;
    ruler.appendChild(tick);
    n++;
  }
  flush();
}

src.addEventListener('input', runStrip);
for (const id of ['opt-aggressive', 'opt-glue', 'opt-nfkc']) {
  $(id).addEventListener('change', runStrip);
}
$('btn-clean').addEventListener('click', runStrip);

$('load-sample').addEventListener('click', () => {
  src.value = watermarkedSample();
  $('sample-note').hidden = false;
  $('sample-note').textContent = SAMPLE_NOTE;
  runStrip();
});

$('copy-out').addEventListener('click', async () => {
  if (!out.value) { toast('Nothing to copy yet.'); return; }
  try {
    await navigator.clipboard.writeText(out.value);
    toast('Cleaned text copied.');
  } catch {
    out.select(); document.execCommand('copy'); toast('Cleaned text copied.');
  }
});

/* ═════════════════════════════════════════════════════ REWRITE (Layer B) ══ */

let currentMode = 'examples';
let abortController = null;
let localGenerate = null;
let loadedLocalId = null;

function currentProvider() {
  return byId($('rw-provider').value) || browserProviders()[0];
}

function setMode(mode) {
  const changed = mode !== currentMode;
  currentMode = mode;
  $('cfg-key').hidden = mode !== 'key';
  $('cfg-local').hidden = mode !== 'local';
  $('cfg-examples').hidden = mode !== 'examples';
  $('cfg-shared').hidden = mode === 'examples';
  $('rw-panes').hidden = mode === 'examples';
  $('rw-controls').hidden = mode === 'examples';

  // A result belongs to the mode that produced it. Leaving a genuine API
  // result on screen after switching to Examples would park it directly above
  // the "these are hand-written, not model output" notice and make the page
  // contradict itself.
  if (changed) {
    $('rw-result').hidden = true;
    $('rw-prompt-block').hidden = true;
    $('rw-progress').textContent = '';
  }

  if (mode === 'local') refreshWebgpu();
}

document.querySelectorAll('input[name="rwmode"]').forEach((r) => {
  r.addEventListener('change', () => r.checked && setMode(r.value));
});

/* provider + model wiring */

function initProviders() {
  const sel = $('rw-provider');
  sel.replaceChildren();
  for (const p of browserProviders()) {
    const opt = el('option', null, p.label);
    opt.value = p.id;
    sel.appendChild(opt);
  }
  sel.value = 'openrouter';
  onProviderChange();
}

async function onProviderChange() {
  const p = currentProvider();
  $('provider-note').textContent = p.freeNote || '';
  $('endpoint-field').hidden = !p.editableEndpoint;
  $('rw-model').value = p.defaultModel || '';

  const key = keystore.getKey(p.id);
  $('rw-key').value = key;
  updateKeyState();

  const line = $('get-key-line');
  line.replaceChildren();
  if (p.keyUrl) {
    line.appendChild(document.createTextNode('Get a free key: '));
    const a = el('a', null, p.keyUrl);
    a.href = p.keyUrl;
    a.target = '_blank';
    a.rel = 'noopener';
    line.appendChild(a);
  }

  fillModels(p, key);
}

async function fillModels(p, key) {
  const list = $('model-list');
  list.replaceChildren();
  for (const m of p.fallbackModels || []) {
    const o = el('option');
    o.value = m;
    list.appendChild(o);
  }
  $('model-note').textContent = p.modelsUrl && key
    ? 'Loading the live model list…'
    : 'Type any model id this provider accepts.';

  if (!p.modelsUrl || !key) return;
  const models = await listModels(p, key);
  list.replaceChildren();
  for (const m of models) {
    const o = el('option');
    o.value = m;
    list.appendChild(o);
  }
  $('model-note').textContent =
    `${models.length} model(s) available` +
    (p.id === 'openrouter' ? ' — free ones only' : '');
}

$('rw-provider').addEventListener('change', onProviderChange);

/* key field */

function updateKeyState() {
  const p = currentProvider();
  const key = $('rw-key').value.trim();
  const state = $('key-state');
  const note = $('key-note');

  if (!key) {
    state.textContent = 'not set';
    note.textContent = '';
    return;
  }
  state.textContent = keystore.mask(key);
  const check = keystore.looksPlausible(p, key);
  note.textContent = check.why || '';
}

$('rw-key').addEventListener('input', () => {
  const p = currentProvider();
  const key = $('rw-key').value.trim();
  keystore.setKey(p.id, key);
  updateKeyState();
});

$('rw-key').addEventListener('change', () => fillModels(currentProvider(), $('rw-key').value.trim()));

$('key-show').addEventListener('click', () => {
  const f = $('rw-key');
  const showing = f.type === 'text';
  f.type = showing ? 'password' : 'text';
  $('key-show').textContent = showing ? 'Show' : 'Hide';
});

$('key-clear').addEventListener('click', () => {
  keystore.clearKey(currentProvider().id);
  $('rw-key').value = '';
  updateKeyState();
  toast('Key forgotten.');
});

$('key-remember').addEventListener('change', (e) => {
  keystore.setRemember(e.target.checked);
  toast(e.target.checked
    ? 'Key will persist in this browser. Anyone using this profile can read it.'
    : 'Key will be dropped when the tab closes.');
});

/* strength + sliders */

function initStrengths() {
  const sel = $('rw-strength');
  sel.replaceChildren();
  for (const s of STRENGTHS) {
    const o = el('option', null, `${s.label} — ${s.blurb}`);
    o.value = s.id;
    sel.appendChild(o);
  }
  sel.addEventListener('change', () => {
    const s = STRENGTHS.find((x) => x.id === sel.value);
    $('strength-note').textContent = s ? s.blurb : '';
  });
  $('strength-note').textContent = STRENGTHS[0].blurb;
}

$('rw-temp').addEventListener('input', (e) => { $('rw-temp-val').value = e.target.value; });
$('rw-cand').addEventListener('input', (e) => { $('rw-cand-val').value = e.target.value; });
$('rw-src').addEventListener('input', () => {
  $('rw-src-count').textContent = `${$('rw-src').value.length} characters`;
});

/* local model */

function initLocalModelSwitching() {
  $('local-model').addEventListener('change', () => {
    if (loadedLocalId && loadedLocalId !== $('local-model').value) {
      localGenerate = null;
      $('local-status').textContent = 'Not loaded. This model will download on first use.';
      $('local-progress').style.width = '0%';
    }
  });
}

async function refreshWebgpu() {
  const warn = $('webgpu-warn');
  const ok = await webgpuUsable();
  warn.hidden = ok;
  if (!ok) {
    warn.textContent =
      'WebGPU is not available in this browser, so the model would run on the ' +
      'CPU — expect it to be very slow. Chrome or Edge on a recent machine ' +
      'gives the best result. Using an API key avoids the download entirely.';
  }
}

function initLocalModels() {
  const sel = $('local-model');
  sel.replaceChildren();
  for (const m of MODELS) {
    const o = el('option', null, `${m.label} (~${m.approxMB} MB)`);
    o.value = m.id;
    sel.appendChild(o);
  }
}

async function ensureLocalModel() {
  const id = $('local-model').value;
  // Keyed on the selection, not merely "have we loaded anything": caching on
  // the latter meant picking a different model after one was already loaded
  // silently kept using the old one. loadLocalModel() caches per id itself.
  if (localGenerate && loadedLocalId === id) return localGenerate;
  loadedLocalId = id;
  const status = $('local-status');
  const fill = $('local-progress');

  localGenerate = await loadLocalModel(id, (p) => {
    if (p.message) status.textContent = p.message;
    if (typeof p.progress === 'number') {
      fill.style.width = `${p.progress}%`;
      const file = p.file ? ` ${p.file}` : '';
      status.textContent = `downloading${file} — ${p.progress.toFixed(0)}%`;
    }
  });
  fill.style.width = '100%';
  status.textContent = 'Model ready.';
  return localGenerate;
}

/* run */

$('rw-preview').addEventListener('click', () => {
  const text = $('rw-src').value;
  if (!text) { toast('Paste some text first.'); return; }
  $('rw-prompt-block').hidden = false;
  $('rw-prompt').textContent = buildPrompt(text, $('rw-strength').value);
});

$('rw-cancel').addEventListener('click', () => {
  abortController?.abort();
  toast('Cancelled.');
});

$('rw-run').addEventListener('click', async () => {
  const text = $('rw-src').value;
  if (!text.trim()) { toast('Paste some text first.'); return; }

  const btn = $('rw-run');
  const provider = currentProvider();
  const key = $('rw-key').value.trim();

  if (currentMode === 'key' && !key) {
    toast('Add your API key first, or switch to the in-browser model.', true);
    return;
  }

  abortController = new AbortController();
  $('rw-cancel').hidden = false;
  busy(btn, true, 'Rewriting…');
  $('rw-progress').textContent = '';

  try {
    let gen = null;
    if (currentMode === 'local') {
      $('rw-progress').textContent = 'preparing the model…';
      gen = await ensureLocalModel();
    }

    const result = await rewrite(text, {
      provider,
      endpoint: provider.editableEndpoint ? $('rw-endpoint').value.trim() : null,
      key,
      model: $('rw-model').value.trim() || provider.defaultModel,
      strength: $('rw-strength').value,
      temperature: parseFloat($('rw-temp').value),
      candidates: parseInt($('rw-cand').value, 10),
      signal: abortController.signal,
      localGenerate: gen,
      onProgress: (m) => { $('rw-progress').textContent = m; },
    });

    $('rw-out').value = result.text;
    $('rw-out-count').textContent = `${result.outputLength} characters`;
    showResult(result);
    $('rw-prompt-block').hidden = false;
    $('rw-prompt').textContent = result.prompt;
    toast('Rewrite complete.');
  } catch (e) {
    if (e.name === 'AbortError') {
      $('rw-progress').textContent = 'cancelled';
    } else {
      toast(e instanceof RewriteError ? e.message : `Rewrite failed: ${e.message}`, true);
      $('rw-progress').textContent = '';
    }
  } finally {
    busy(btn, false);
    $('rw-cancel').hidden = true;
    abortController = null;
  }
});

function showResult(r) {
  $('rw-result').hidden = false;
  $('rw-stats').textContent =
    `${r.provider} · ${r.model || 'in-browser'} · ${r.strength} · ` +
    `${r.inputLength} → ${r.outputLength} characters`;

  const pct = Math.round(r.divergence * 100);
  $('div-meter').hidden = false;
  const fill = $('div-fill');
  fill.style.width = `${pct}%`;
  fill.className = 'meter-fill' + (pct >= 70 ? ' good' : pct >= 50 ? ' mid' : '');
  $('div-label').textContent = `${pct}% diverged`;

  const notes = $('rw-notes');
  notes.replaceChildren();
  for (const n of r.notes) notes.appendChild(el('li', null, n));
  if (r.candidates.length > 1) {
    notes.appendChild(el('li', null,
      'Candidate divergences: ' +
      r.candidates.map((c) => `${Math.round(c.divergence * 100)}%`).join(', ') +
      '. Kept the highest.'));
  }
}

$('rw-copy').addEventListener('click', async () => {
  const text = $('rw-out').value;
  if (!text) { toast('Nothing to copy yet.'); return; }
  try {
    await navigator.clipboard.writeText(text);
    toast('Rewritten text copied.');
  } catch {
    $('rw-out').select(); document.execCommand('copy'); toast('Rewritten text copied.');
  }
});

/* worked examples */

function renderExamples() {
  const host = $('examples-list');
  host.replaceChildren();

  for (const ex of EXAMPLES) {
    const card = el('div', 'example');

    const head = el('div', 'example-head');
    head.appendChild(el('h4', null, ex.title));
    const tag = el('span', 'kindtag', ex.strength);
    tag.dataset.kind = 'zwj_family';
    head.appendChild(tag);
    const d = Math.round(divergence(ex.before, ex.after) * 100);
    head.appendChild(el('span', 'pane-meta', `${d}% diverged`));
    const useBtn = el('button', 'mini', 'Try this text');
    useBtn.addEventListener('click', () => {
      $('rw-src').value = ex.before;
      $('rw-src-count').textContent = `${ex.before.length} characters`;
      $('rw-strength').value = ex.strength;
      $('strength-note').textContent =
        STRENGTHS.find((s) => s.id === ex.strength)?.blurb || '';
      document.querySelector('input[name="rwmode"][value="key"]').checked = true;
      setMode('key');
      $('rw-src').scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    head.appendChild(useBtn);
    card.appendChild(head);

    const body = el('div', 'example-body');
    const before = el('div', 'example-side');
    before.appendChild(el('span', 'example-label', 'Before'));
    before.appendChild(document.createTextNode(ex.before));
    const after = el('div', 'example-side');
    after.appendChild(el('span', 'example-label', 'After'));
    after.appendChild(document.createTextNode(ex.after));
    body.append(before, after);
    card.appendChild(body);

    if (ex.note) card.appendChild(el('div', 'example-note', ex.note));
    host.appendChild(card);
  }
}

/* ═════════════════════════════════════════════════════════════ EXAMPLES ══ */

const DEMOS = [
  {
    title: 'A hidden tracking ID',
    body: 'Text can carry an invisible identifier built from Unicode tag characters. It survives copy and paste and shows up nowhere on screen.',
    make: () => 'Quarterly Report' +
      [...'ID:4471'].map((c) => String.fromCodePoint(0xe0000 + c.codePointAt(0))).join(''),
  },
  {
    title: 'Zero-width spacing',
    body: 'Zero-width spaces and word joiners scattered through a paragraph encode a pattern without changing how it reads.',
    make: () => 'The​ board⁠ approved​ the⁠ budget​ on Tuesday.',
  },
  {
    title: 'Homoglyph substitution',
    body: 'Cyrillic letters that look identical to Latin ones. Try it, then turn on Aggressive mode to fold them back.',
    make: () => 'Bank of Amerіca sent the invoice.',
  },
  {
    title: 'Legitimate joiners kept',
    body: 'Not every invisible character is a watermark. Emoji sequences and Persian text need theirs. This sample should come back unchanged.',
    make: () => 'family \u{1F469}‍\u{1F467} and Persian پ‌ر',
  },
];

function renderDemoCards() {
  const host = $('demo-cards');
  host.replaceChildren();
  for (const d of DEMOS) {
    const card = el('div', 'card on');
    const h = el('h4', null, d.title);
    card.appendChild(h);
    card.appendChild(el('p', null, d.body));
    const btn = el('button', 'mini', 'Load into Strip');
    btn.style.marginTop = '10px';
    btn.addEventListener('click', () => {
      src.value = d.make();
      $('sample-note').hidden = true;
      runStrip();
      openTab('strip');
      src.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    card.appendChild(btn);
    host.appendChild(card);
  }
}

function renderProviderTable() {
  const tbody = $('provider-table').querySelector('tbody');
  tbody.replaceChildren();
  for (const p of PROVIDERS) {
    if (p.id === 'openai-compatible') continue;
    const tr = el('tr');
    tr.appendChild(el('td', null, p.label));
    const yn = el('td');
    yn.appendChild(el('span', p.browserDirect ? 'yes' : 'no',
      p.browserDirect ? 'yes' : 'no — local app only'));
    tr.appendChild(yn);
    tr.appendChild(el('td', 'uname', p.freeNote || ''));
    tbody.appendChild(tr);
  }
}

/* ══════════════════════════════════════════════════════════════ startup ══ */

function boot() {
  initProviders();
  initStrengths();
  initLocalModels();
  initLocalModelSwitching();
  renderExamples();
  renderDemoCards();
  renderProviderTable();

  $('key-remember').checked = keystore.rememberEnabled();
  setMode('examples');

  // ?sample=1 preloads the marked sample, so a link can show the tool doing
  // something rather than landing on an empty box.
  if (new URLSearchParams(location.search).get('sample') === '1') {
    src.value = watermarkedSample();
    $('sample-note').hidden = false;
    $('sample-note').textContent = SAMPLE_NOTE;
  }
  runStrip();

  const hash = location.hash.replace('#', '');
  if (TABS.includes(hash)) openTab(hash);
}

boot();
