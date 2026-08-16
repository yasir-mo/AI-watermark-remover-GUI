/* wmtk web client. No framework, no build step, no external requests. */
'use strict';

const $ = (id) => document.getElementById(id);

/* Bearer token for this service, if the operator set one. Held in memory and
   in sessionStorage only, so it dies with the tab. */
let SERVICE_TOKEN = sessionStorage.getItem('wmtk_token') || '';

/* Rewrite-backend key. Held in memory only. Never persisted, never logged. */
let REWRITE_KEY = '';

/* ────────────────────────────────────────────────────── plumbing */

async function api(path, body) {
  const opts = { headers: {} };
  if (SERVICE_TOKEN) opts.headers['Authorization'] = 'Bearer ' + SERVICE_TOKEN;
  if (body !== undefined) {
    opts.method = 'POST';
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (e) {
    throw new Error('Cannot reach the service. Is it still running?');
  }
  const text = await resp.text();
  let data;
  try { data = JSON.parse(text); } catch { data = null; }
  if (!resp.ok) {
    if (resp.status === 401) {
      const t = prompt('This service requires a bearer token.');
      if (t) { SERVICE_TOKEN = t; sessionStorage.setItem('wmtk_token', t); return api(path, body); }
    }
    const msg = (data && data.error) || `HTTP ${resp.status}`;
    const detail = (data && data.detail) ? `: ${data.detail}` : '';
    throw new Error(msg + detail);
  }
  return data;
}

let toastTimer;
function toast(msg, isError) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('err', !!isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 7000 : 3200);
}

function busy(btn, on, label) {
  if (on) {
    btn.dataset.label = btn.innerHTML;
    btn.innerHTML = '<span class="spin"></span>' + (label || 'Working');
    btn.disabled = true;
  } else {
    btn.innerHTML = btn.dataset.label || btn.innerHTML;
    btn.disabled = false;
  }
}

function b64encode(bytes) {
  let s = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(s);
}

function b64decode(str) {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const readBytes = (file) => new Promise((res, rej) => {
  const r = new FileReader();
  r.onload = () => res(new Uint8Array(r.result));
  r.onerror = () => rej(new Error('Cannot read ' + file.name));
  r.readAsArrayBuffer(file);
});

function bytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}

function download(u8, name) {
  const url = URL.createObjectURL(new Blob([u8]));
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

function cleanedName(name) {
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(0, dot) + '.cleaned' + name.slice(dot) : name + '.cleaned';
}

/* ─────────────────────────────────────────────────────── tabs */

document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => {
      const on = t === tab;
      t.classList.toggle('is-active', on);
      t.setAttribute('aria-selected', String(on));
    });
    ['text', 'files', 'batch', 'caps'].forEach((n) => {
      $('tab-' + n).hidden = n !== tab.dataset.tab;
    });
    if (tab.dataset.tab === 'caps') loadCapabilitiesTab();
  });
});

/* ───────────────────────────────────── codepoint short labels */

const SHORT = {
  0x00AD: 'SHY', 0x034F: 'CGJ', 0x061C: 'ALM',
  0x200B: 'ZWSP', 0x200C: 'ZWNJ', 0x200D: 'ZWJ', 0x200E: 'LRM', 0x200F: 'RLM',
  0x2028: 'LSEP', 0x2029: 'PSEP',
  0x202A: 'LRE', 0x202B: 'RLE', 0x202C: 'PDF', 0x202D: 'LRO', 0x202E: 'RLO',
  0x2060: 'WJ', 0x2061: 'FA', 0x2062: 'IT', 0x2063: 'ISEP', 0x2064: 'IPLUS',
  0x2066: 'LRI', 0x2067: 'RLI', 0x2068: 'FSI', 0x2069: 'PDI',
  0xFEFF: 'BOM', 0x00A0: 'NBSP', 0x202F: 'NNBSP', 0x205F: 'MMSP',
  0x3000: 'IDSP', 0x1680: 'OGSP', 0xFFF9: 'IAA', 0xFFFA: 'IAS', 0xFFFB: 'IAT',
};

function shortLabel(cp, name) {
  if (SHORT[cp]) return SHORT[cp];
  if (cp >= 0xFE00 && cp <= 0xFE0F) return 'VS' + (cp - 0xFE00 + 1);
  if (cp >= 0xE0100 && cp <= 0xE01EF) return 'VS' + (cp - 0xE0100 + 17);
  if (cp >= 0xE0020 && cp <= 0xE007F) return 'TAG';
  if (cp === 0xE0001) return 'LTAG';
  if (cp >= 0x2000 && cp <= 0x200A) return 'SP' + (cp - 0x2000);
  if (name && name.startsWith('<')) return 'PUA';
  if (name) {
    const initials = name.split(/[\s-]+/).map((w) => w[0]).join('');
    return initials.length > 1 ? initials.slice(0, 5) : name.slice(0, 4);
  }
  return 'U+' + cp.toString(16).toUpperCase();
}

const hex = (cp) => 'U+' + cp.toString(16).toUpperCase().padStart(4, '0');

/* ══════════════════════════════════════════════════ TEXT MODE */

const src = $('src');
const out = $('out');
let lastScan = null;

src.addEventListener('input', () => {
  $('src-count').textContent = [...src.value].length + ' characters';
});

function textOptions(extra) {
  return Object.assign({
    aggressive: $('opt-aggressive').checked,
    strip_emoji_glue: $('opt-glue').checked,
    normalize: $('opt-nfkc').checked,
  }, extra || {});
}

$('btn-inspect').addEventListener('click', async () => {
  if (!src.value) { toast('Paste some text first.'); return; }
  const btn = $('btn-inspect');
  busy(btn, true, 'Inspecting');
  try {
    const r = await api('/inspect', {
      text: src.value, name: 'input.txt', options: textOptions(),
    });
    lastScan = r.layer_a;
    renderScan(r.layer_a, src.value);
    $('diff-block').hidden = true;
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy(btn, false);
  }
});

$('btn-clean').addEventListener('click', async () => {
  if (!src.value) { toast('Paste some text first.'); return; }
  const btn = $('btn-clean');
  busy(btn, true, 'Cleaning');
  try {
    const r = await api('/clean', {
      text: src.value, name: 'input.txt', options: textOptions({ diff: true }),
    });
    const text = new TextDecoder().decode(b64decode(r.cleaned));
    out.value = text;
    const la = r.report.layer_a;
    $('out-count').textContent =
      `${[...text].length} characters · ${la.total_removed} removed · ${la.total_replaced} replaced`;
    renderDiff(r.report.diff || []);
    const scan = await api('/inspect', {
      text: src.value, name: 'input.txt', options: textOptions(),
    });
    renderScan(scan.layer_a, src.value);
    const kept = Object.values(la.kept_load_bearing || {}).reduce((a, b) => a + b, 0);
    toast(kept
      ? `Removed ${la.total_removed}, replaced ${la.total_replaced}, kept ${kept} that text rendering needs.`
      : `Removed ${la.total_removed}, replaced ${la.total_replaced}.`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy(btn, false);
  }
});

$('copy-out').addEventListener('click', async () => {
  if (!out.value) { toast('Nothing to copy yet.'); return; }
  try {
    await navigator.clipboard.writeText(out.value);
    toast('Cleaned text copied.');
  } catch {
    out.select(); document.execCommand('copy');
    toast('Cleaned text copied.');
  }
});

function renderScan(la, text) {
  if (!la) return;
  const tbody = $('findings').querySelector('tbody');
  tbody.textContent = '';

  $('findings-block').hidden = false;
  $('findings-summary').textContent = la.findings.length
    ? `${la.total_flagged} flagged, ${la.total_removable} removable across ${la.findings.length} distinct codepoint(s).`
    : 'Nothing hidden in this text.';

  for (const f of la.findings) {
    const tr = document.createElement('tr');

    const cp = document.createElement('td');
    cp.innerHTML = `<span class="cp">${f.codepoint}</span>`;
    tr.appendChild(cp);

    const nm = document.createElement('td');
    nm.className = 'uname';
    nm.textContent = f.name;
    tr.appendChild(nm);

    const kd = document.createElement('td');
    const tag = document.createElement('span');
    tag.className = 'kindtag';
    tag.dataset.kind = f.kind;
    tag.textContent = f.kind;
    kd.appendChild(tag);
    if (f.confidence !== 'probable') {
      const c = document.createElement('div');
      c.className = 'offsets';
      c.textContent = f.confidence;
      kd.appendChild(c);
    }
    tr.appendChild(kd);

    const ct = document.createElement('td');
    ct.className = 'num'; ct.textContent = f.count;
    tr.appendChild(ct);

    const kp = document.createElement('td');
    kp.className = 'num';
    if (f.kept_as_load_bearing) {
      kp.innerHTML = `<span class="kept-note">${f.kept_as_load_bearing}</span>`;
      kp.title = 'Preserved: ' + Object.keys(f.kept_reasons).join(', ');
    } else {
      kp.textContent = '0';
    }
    tr.appendChild(kp);

    const of = document.createElement('td');
    of.className = 'offsets';
    of.textContent = f.offsets.join(', ') + (f.offsets_truncated ? ', …' : '');
    tr.appendChild(of);

    tbody.appendChild(tr);
  }

  renderReveal(la, text);
}

/* The signature: hidden characters materialized in place. */
function renderReveal(la, text) {
  const block = $('reveal-block');
  const host = $('reveal');
  const ruler = $('ruler');
  host.textContent = '';
  ruler.textContent = '';

  if (!la.findings.length) { block.hidden = true; return; }
  block.hidden = false;

  const meta = new Map();
  for (const f of la.findings) meta.set(f.codepoint_int, f);

  const chars = [...text];
  const total = chars.length || 1;
  let plain = '';
  let n = 0;
  let offset = 0;

  const flush = () => {
    if (plain) { host.appendChild(document.createTextNode(plain)); plain = ''; }
  };

  for (const ch of chars) {
    const cp = ch.codePointAt(0);
    const f = meta.get(cp);
    if (!f) { plain += ch; offset++; continue; }

    flush();
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.dataset.kind = f.kind;
    chip.textContent = shortLabel(cp, f.name);
    chip.title = `${f.codepoint} ${f.name}  ·  ${f.kind}  ·  offset ${offset}`;
    if (n < 60) chip.style.setProperty('--d', (n * 12) + 'ms');
    host.appendChild(chip);

    // Spaces keep their visible width alongside the chip.
    if (f.kind === 'space') host.appendChild(document.createTextNode(' '));

    const tick = document.createElement('span');
    tick.className = 'tick';
    tick.style.left = (offset / total * 100) + '%';
    tick.style.background = `var(--k-${f.kind === 'zwj_family' ? 'zwj'
      : f.kind === 'tag_chars' ? 'tag'
      : f.kind === 'variation_selector' ? 'vs'
      : f.kind === 'private_use' ? 'pua'
      : f.kind === 'other_cf' ? 'other'
      : f.kind === 'confusable' ? 'conf'
      : f.kind})`;
    ruler.appendChild(tick);

    n++; offset++;
  }
  flush();
}

function renderDiff(ops) {
  const host = $('diff');
  host.textContent = '';
  if (!ops.length) { $('diff-block').hidden = true; return; }
  $('diff-block').hidden = false;

  for (const op of ops) {
    if (op.op === 'equal') {
      host.appendChild(document.createTextNode(op.text));
    } else if (op.op === 'delete') {
      host.appendChild(mark('d-del', visible(op.text), op.text));
    } else if (op.op === 'insert') {
      host.appendChild(mark('d-ins', visible(op.text), op.text));
    } else {
      host.appendChild(mark('d-rep', visible(op.text) + '→' + visible(op.with), op.text));
    }
  }
}

function mark(cls, label, raw) {
  const s = document.createElement('span');
  s.className = cls;
  s.textContent = label;
  s.title = [...raw].map((c) => hex(c.codePointAt(0))).join(' ');
  return s;
}

function visible(s) {
  return [...s].map((c) => {
    const cp = c.codePointAt(0);
    if (cp === 0x20) return '␠';
    if (cp < 0x20 || (cp >= 0x7F && cp <= 0xA0) || SHORT[cp]
        || (cp >= 0x2000 && cp <= 0x206F) || cp === 0xFEFF
        || (cp >= 0xFE00 && cp <= 0xFE0F) || cp >= 0xE0000) {
      return shortLabel(cp, '');
    }
    return c;
  }).join('');
}

/* ══════════════════════════════════════════════════ FILE MODE */

let currentFile = null;

function wireDrop(zone, input, handler, multiple) {
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ['dragenter', 'dragover'].forEach((ev) => zone.addEventListener(ev, (e) => {
    stop(e); zone.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((ev) => zone.addEventListener(ev, (e) => {
    stop(e); zone.classList.remove('is-over');
  }));
  zone.addEventListener('drop', (e) => {
    const files = [...e.dataTransfer.files];
    if (files.length) handler(multiple ? files : files[0]);
  });
  zone.addEventListener('click', (e) => {
    if (e.target.tagName !== 'BUTTON') input.click();
  });
  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  input.addEventListener('change', () => {
    const files = [...input.files];
    if (files.length) handler(multiple ? files : files[0]);
    input.value = '';
  });
}

wireDrop($('drop'), $('file-input'), inspectFile, false);
$('browse').addEventListener('click', (e) => { e.stopPropagation(); $('file-input').click(); });

async function inspectFile(file) {
  try {
    const data = await readBytes(file);
    currentFile = { file, data };
    $('file-result').hidden = false;
    $('f-after-block').hidden = true;
    $('f-name').textContent = file.name;
    $('f-size').textContent = bytes(data.length);
    $('f-format').textContent = '…';
    $('f-verdict').className = 'verdict';
    $('f-verdict').innerHTML = '<span class="spin"></span>Inspecting';

    const r = await api('/inspect', {
      file: b64encode(data), name: file.name, options: textOptions(),
    });
    currentFile.report = r;
    renderFileReport(r);
  } catch (e) {
    toast(e.message, true);
    $('f-verdict').className = 'verdict hit';
    $('f-verdict').textContent = e.message;
  }
}

function renderFileReport(r) {
  $('f-format').textContent = r.format;

  const s = r.summary;
  const v = $('f-verdict');
  // Colour on findings_present. A file carrying an
  // author name has something to strip even though it carries no watermark.
  v.className = 'verdict ' + (s.findings_present ? 'hit' : 'clear');
  const parts = [];
  if (s.c2pa_present) parts.push('a <strong>C2PA manifest</strong>');
  if (s.metadata_findings) parts.push(`<strong>${s.metadata_findings}</strong> metadata item(s)`);
  if (s.invisible_chars) parts.push(`<strong>${s.invisible_chars}</strong> invisible character(s)`);
  v.innerHTML = parts.length
    ? 'Found ' + parts.join(', ') + '. Clean the file to strip them.'
    : 'Nothing found in the layers this service can read.';

  const meta = r.metadata;
  const metaList = $('f-meta');
  metaList.textContent = '';
  $('f-meta-sub').textContent = meta
    ? `${meta.count} item(s) · handler tier: ${meta.tier}`
    : 'not scanned';

  if (meta) {
    if (!meta.findings.length) {
      metaList.appendChild(emptyItem('No metadata found.'));
    }
    for (const f of meta.findings.slice(0, 200)) {
      metaList.appendChild(findItem(f.kind, f.detail));
    }
    for (const w of meta.warnings) metaList.appendChild(findItem('warning', w));
  }

  const laList = $('f-la');
  laList.textContent = '';
  if (r.layer_a) {
    const la = r.layer_a;
    $('f-la-sub').textContent =
      `${la.total_flagged} flagged, ${la.total_removable} removable`;
    if (!la.findings.length) laList.appendChild(emptyItem('No invisible characters.'));
    for (const f of la.findings) {
      laList.appendChild(findItem(
        f.kind,
        `${f.codepoint} ${f.name} ×${f.count}` +
        (f.kept_as_load_bearing ? ` (${f.kept_as_load_bearing} kept, text needs them)` : '')
      ));
    }
  } else {
    $('f-la-sub').textContent = r.is_text_format
      ? 'not scanned'
      : 'not applicable to this format';
    laList.appendChild(emptyItem(
      r.is_text_format ? 'not scanned' : 'Binary format. Layer A applies to text only.'
    ));
  }
}

function findItem(kind, detail) {
  const li = document.createElement('li');
  const tag = document.createElement('span');
  tag.className = 'kindtag';
  tag.dataset.kind = kind;
  tag.textContent = kind;
  li.appendChild(tag);
  const d = document.createElement('span');
  d.className = 'detail';
  d.textContent = detail;
  li.appendChild(d);
  return li;
}

function emptyItem(text) {
  const li = document.createElement('li');
  li.className = 'empty';
  li.textContent = text;
  return li;
}

$('btn-file-clean').addEventListener('click', async () => {
  if (!currentFile) return;
  const btn = $('btn-file-clean');
  busy(btn, true, 'Cleaning');
  try {
    const r = await api('/clean', {
      file: b64encode(currentFile.data),
      name: currentFile.file.name,
      options: textOptions(),
    });
    const cleaned = b64decode(r.cleaned);
    download(cleaned, cleanedName(currentFile.file.name));

    const after = $('f-after');
    after.textContent = '';
    $('f-after-block').hidden = false;
    const rep = r.report;
    after.appendChild(findItem('size',
      `${bytes(rep.input_bytes)} → ${bytes(rep.output_bytes)} (${rep.bytes_removed} bytes removed)`));
    for (const a of (rep.metadata ? rep.metadata.actions : [])) {
      after.appendChild(findItem('action', a));
    }
    if (rep.layer_a) {
      after.appendChild(findItem('layer_a',
        `${rep.layer_a.total_removed} characters removed, ${rep.layer_a.total_replaced} replaced`));
    }
    for (const w of (rep.metadata ? rep.metadata.warnings : [])) {
      after.appendChild(findItem('warning', w));
    }
    if (!rep.metadata && !rep.layer_a) after.appendChild(emptyItem('Nothing to change.'));
    toast('Cleaned file downloaded.');
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy(btn, false);
  }
});

/* ═════════════════════════════════════════════════════ BATCH */

let batch = [];

wireDrop($('drop-batch'), $('file-input-batch'), addBatch, true);
$('browse-batch').addEventListener('click', (e) => {
  e.stopPropagation(); $('file-input-batch').click();
});

async function addBatch(files) {
  for (const file of files) {
    batch.push({ file, status: 'pending', meta: '?', inv: '?', cleaned: null });
  }
  $('batch-bar').hidden = false;
  $('batch-wrap').hidden = false;
  renderBatch();
  for (const row of batch) {
    if (row.status === 'pending') await inspectRow(row);
  }
}

async function inspectRow(row) {
  row.status = 'inspecting';
  renderBatch();
  try {
    const data = await readBytes(row.file);
    row.data = data;
    const r = await api('/inspect', {
      file: b64encode(data), name: row.file.name, options: textOptions(),
    });
    row.format = r.format;
    row.meta = r.summary.metadata_findings;
    row.inv = r.summary.invisible_chars;
    row.suspect = r.summary.findings_present;
    row.status = 'inspected';
  } catch (e) {
    row.status = 'error';
    row.error = e.message;
  }
  renderBatch();
}

async function cleanRow(row) {
  if (!row.data) return;
  row.status = 'cleaning';
  renderBatch();
  try {
    const r = await api('/clean', {
      file: b64encode(row.data), name: row.file.name, options: textOptions(),
    });
    row.cleaned = b64decode(r.cleaned);
    row.status = 'done';
  } catch (e) {
    row.status = 'error';
    row.error = e.message;
  }
  renderBatch();
}

function renderBatch() {
  const tbody = $('batch-table').querySelector('tbody');
  tbody.textContent = '';
  for (const row of batch) {
    const tr = document.createElement('tr');

    const n = document.createElement('td');
    n.textContent = row.file.name;
    n.style.wordBreak = 'break-all';
    tr.appendChild(n);

    const f = document.createElement('td');
    f.className = 'offsets';
    f.textContent = row.format || '?';
    tr.appendChild(f);

    const m = document.createElement('td');
    m.className = 'num'; m.textContent = row.meta;
    tr.appendChild(m);

    const i = document.createElement('td');
    i.className = 'num'; i.textContent = row.inv;
    tr.appendChild(i);

    const s = document.createElement('td');
    const sp = document.createElement('span');
    sp.className = 'status';
    sp.dataset.s = row.status === 'inspected' ? 'pending' : row.status;
    sp.textContent = row.status === 'inspected'
      ? (row.suspect ? 'ready · found marks' : 'ready · clean')
      : row.status;
    if (row.error) sp.title = row.error;
    s.appendChild(sp);
    tr.appendChild(s);

    const a = document.createElement('td');
    if (row.cleaned) {
      const b = document.createElement('button');
      b.className = 'mini';
      b.textContent = 'Download';
      b.addEventListener('click', () => download(row.cleaned, cleanedName(row.file.name)));
      a.appendChild(b);
    } else if (row.status === 'inspected') {
      const b = document.createElement('button');
      b.className = 'mini';
      b.textContent = 'Clean';
      b.addEventListener('click', () => cleanRow(row));
      a.appendChild(b);
    }
    tr.appendChild(a);
    tbody.appendChild(tr);
  }

  const done = batch.filter((r) => r.status === 'done' || r.status === 'error').length;
  const pct = batch.length ? (done / batch.length * 100) : 0;
  $('progress-fill').style.width = pct + '%';
  $('progress-text').textContent = batch.length
    ? `${done} of ${batch.length} cleaned`
    : 'no files queued';
}

$('btn-clean-all').addEventListener('click', async () => {
  const btn = $('btn-clean-all');
  busy(btn, true, 'Cleaning');
  try {
    for (const row of batch) {
      if (row.status === 'inspected') await cleanRow(row);
    }
    const ok = batch.filter((r) => r.cleaned).length;
    toast(`${ok} file(s) cleaned. Download each from the table.`);
  } finally {
    busy(btn, false);
  }
});

$('btn-clear-batch').addEventListener('click', () => {
  batch = [];
  renderBatch();
  $('batch-bar').hidden = true;
  $('batch-wrap').hidden = true;
});

/* ══════════════════════════════════════════════ REWRITE DRAWER */

const drawer = $('drawer');
const scrim = $('scrim');

function openDrawer() {
  drawer.hidden = false; scrim.hidden = false;
  $('rw-backend').focus();
}
function closeDrawer() { drawer.hidden = true; scrim.hidden = true; }

$('btn-rewrite-open').addEventListener('click', () => {
  if (!src.value) { toast('Paste some text first.'); return; }
  openDrawer();
});
$('drawer-close').addEventListener('click', closeDrawer);
scrim.addEventListener('click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !drawer.hidden) closeDrawer();
});

$('rw-temp').addEventListener('input', (e) => { $('rw-temp-val').value = e.target.value; });
$('rw-cand').addEventListener('input', (e) => { $('rw-cand-val').value = e.target.value; });
$('rw-key').addEventListener('input', (e) => { REWRITE_KEY = e.target.value; });

function rewritePayload() {
  const p = {
    text: src.value,
    backend: $('rw-backend').value,
    model: $('rw-model').value,
    base_url: $('rw-url').value,
    strength: $('rw-strength').value,
    temperature: parseFloat($('rw-temp').value),
    candidates: parseInt($('rw-cand').value, 10),
    allow_remote: $('rw-remote').checked,
    layer_a_after: $('rw-layer-a').checked,
  };
  if (REWRITE_KEY) p.api_key = REWRITE_KEY;
  return p;
}

$('rw-preview').addEventListener('click', async () => {
  const btn = $('rw-preview');
  busy(btn, true, 'Building');
  try {
    const p = rewritePayload();
    p.backend = 'print-prompt';
    delete p.api_key;
    const r = await api('/rewrite', p);
    $('rw-out-block').hidden = false;
    $('rw-stats').textContent = 'This is the prompt. No model was called.';
    $('rw-out').textContent = r.prompt;
    $('rw-notes').textContent = '';
    $('rw-accept').disabled = true;
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy(btn, false);
  }
});

$('rw-run').addEventListener('click', async () => {
  const btn = $('rw-run');
  busy(btn, true, 'Rewriting');
  try {
    const r = await api('/rewrite', rewritePayload());
    $('rw-out-block').hidden = false;
    $('rw-accept').disabled = false;
    $('rw-out').textContent = r.text;
    $('rw-stats').textContent =
      `${r.backend} · ${r.model} · ${r.strength} · divergence ${(r.divergence * 100).toFixed(0)}%` +
      ` · ${r.input_length} → ${r.output_length} characters`;

    const notes = $('rw-notes');
    notes.textContent = '';
    for (const n of r.notes) {
      const li = document.createElement('li');
      li.textContent = n;
      notes.appendChild(li);
    }
    if (r.candidates.length > 1) {
      const li = document.createElement('li');
      li.textContent = 'Candidate divergences: ' +
        r.candidates.map((c) => (c.divergence * 100).toFixed(0) + '%').join(', ') +
        '. Kept the highest.';
      notes.appendChild(li);
    }
    if (r.layer_a_after && r.layer_a_after.total_removed) {
      const li = document.createElement('li');
      li.textContent =
        `Layer A removed ${r.layer_a_after.total_removed} invisible character(s) the rewrite model added.`;
      notes.appendChild(li);
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy(btn, false);
  }
});

$('rw-accept').addEventListener('click', () => {
  out.value = $('rw-out').textContent;
  $('out-count').textContent = [...out.value].length + ' characters · rewritten';
  closeDrawer();
  toast('Rewritten text moved to the Cleaned pane.');
});

$('rw-discard').addEventListener('click', () => {
  $('rw-out-block').hidden = true;
  $('rw-out').textContent = '';
});

/* ═══════════════════════════════════════════════ CAPABILITIES */

let capsLoaded = false;

async function loadCapabilitiesTab() {
  if (capsLoaded) return;
  try {
    const c = await api('/capabilities');
    renderCards($('caps-tools'), Object.entries(c.tools).map(([name, t]) => ({
      name,
      on: t.installed,
      state: t.installed ? 'installed' : 'missing',
      version: t.version,
      body: t.enables,
      code: t.installed ? null : t.install,
    })));

    renderCards($('caps-backends'), Object.entries(c.backends).map(([name, b]) => ({
      name,
      on: b.configured,
      state: b.configured ? 'configured' : 'not set',
      version: b.configured ? b.path : b.env_var,
      body: b.enables,
      code: b.configured ? null : b.install,
    })));

    renderCards($('caps-misc'), [
      {
        name: 'PDF handling', on: c.pdf_tier !== 'stdlib', state: c.pdf_tier,
        body: c.pdf_tier === 'exiftool+qpdf'
          ? 'Structural rebuild. The metadata bytes leave the file.'
          : c.pdf_tier === 'exiftool'
            ? 'Incremental edit. Old metadata can remain in the file unreferenced. Install qpdf for a full rebuild.'
            : 'Regex fallback. Metadata inside object streams is out of reach. Install exiftool and qpdf.',
      },
      {
        name: 'Layer B', on: true, state: 'ready',
        body: `Backends: ${c.layer_b.backends.join(', ')}. Strengths: ${c.layer_b.strengths.join(', ')}.`,
        code: `export ${c.layer_b.api_key_env}=…   # server-side key`,
      },
      {
        name: 'Limits', on: true, state: 'enforced',
        body: `Maximum ${bytes(c.limits.max_input_bytes)} per file, ${bytes(c.limits.max_text_bytes)} per text payload.`,
      },
      {
        name: 'Formats', on: true, state: `${c.formats.length} supported`,
        body: c.formats.join(' · '),
      },
    ]);

    const spec = await api('/openapi.json');
    const host = $('spec');
    host.textContent = '';
    for (const [path, ops] of Object.entries(spec.paths)) {
      for (const [verb, op] of Object.entries(ops)) {
        const row = document.createElement('div');
        row.className = 'route';
        const v = document.createElement('span');
        v.className = 'verb'; v.dataset.v = verb; v.textContent = verb.toUpperCase();
        const p = document.createElement('span');
        p.className = 'path'; p.textContent = path;
        const s = document.createElement('span');
        s.className = 'sum'; s.textContent = op.summary || '';
        row.append(v, p, s);
        host.appendChild(row);
      }
    }
    capsLoaded = true;
  } catch (e) {
    toast(e.message, true);
  }
}

function renderCards(host, items) {
  host.textContent = '';
  for (const it of items) {
    const card = document.createElement('div');
    card.className = 'card ' + (it.on ? 'on' : 'off');

    const h = document.createElement('h4');
    h.textContent = it.name;
    const st = document.createElement('span');
    st.className = 'state'; st.textContent = it.state;
    h.appendChild(st);
    card.appendChild(h);

    if (it.version) {
      const v = document.createElement('div');
      v.className = 'ver'; v.textContent = it.version;
      card.appendChild(v);
    }
    const p = document.createElement('p');
    p.textContent = it.body;
    card.appendChild(p);

    if (it.code) {
      const c = document.createElement('code');
      c.textContent = it.code;
      card.appendChild(c);
    }
    host.appendChild(card);
  }
}

/* ══════════════════════════════════════════════════ bootstrap */

(async function boot() {
  try {
    const h = await fetch('/health').then((r) => r.json());
    $('version').textContent = 'v' + h.version;
  } catch {
    $('version').textContent = 'offline';
  }
  try {
    const c = await api('/capabilities');
    const lamps = $('lamps');
    lamps.textContent = '';
    const entries = [
      ...Object.entries(c.tools).map(([n, t]) => [n, t.installed]),
      ['markllm', c.backends['harnesses.markllm'].configured],
    ];
    for (const [name, on] of entries) {
      const el = document.createElement('span');
      el.className = 'lamp' + (on ? ' on' : '');
      el.textContent = name;
      el.title = on ? `${name} is available` : `${name} is not installed. See Capabilities`;
      lamps.appendChild(el);
    }
  } catch (e) {
    $('lamps').textContent = '';
  }
})();
