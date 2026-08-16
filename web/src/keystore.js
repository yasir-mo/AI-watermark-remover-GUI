/* Where the visitor's API key lives.
 *
 * Threat model for a static BYOK site: there is no server to breach, so the
 * only realistic way a key leaks is script injection on this page. That shapes
 * every decision here.
 *
 *   - sessionStorage by default, so the key dies when the tab closes
 *   - localStorage only on an explicit, revocable opt-in
 *   - never in a URL, a query string, or the document title, where it would
 *     land in history and referrer headers
 *   - one key per provider, so switching providers cannot send the wrong key
 *     to the wrong host
 *
 * The page's CSP forbids third-party script, and the app never assigns
 * untrusted strings to innerHTML, which together are what actually keep this
 * safe. Storage choice is a second layer of defence.
 */

const PREFIX = 'wmtk.key.';
const REMEMBER_FLAG = 'wmtk.remember';

const session = () => globalThis.sessionStorage;
const local = () => globalThis.localStorage;

export function rememberEnabled() {
  try {
    return local().getItem(REMEMBER_FLAG) === '1';
  } catch {
    return false;
  }
}

export function setRemember(on) {
  try {
    if (on) {
      local().setItem(REMEMBER_FLAG, '1');
      // Promote anything already held for this session.
      for (const { provider, key } of listSession()) {
        local().setItem(PREFIX + provider, key);
      }
    } else {
      local().removeItem(REMEMBER_FLAG);
      // Demote the key instead of deleting it. A key loaded from localStorage
      // in a fresh tab was never written to sessionStorage, so dropping the
      // persisted copy would remove it completely, while the UI promises it
      // survives until the tab closes. Copy it down first.
      for (const id of persistedProviders()) {
        const existing = local().getItem(PREFIX + id);
        if (existing && !session().getItem(PREFIX + id)) {
          session().setItem(PREFIX + id, existing);
        }
        local().removeItem(PREFIX + id);
      }
    }
  } catch { /* storage unavailable; keys stay in memory only */ }
}

function listSession() {
  const out = [];
  try {
    for (let i = 0; i < session().length; i++) {
      const k = session().key(i);
      if (k?.startsWith(PREFIX)) {
        out.push({ provider: k.slice(PREFIX.length), key: session().getItem(k) });
      }
    }
  } catch { /* ignore */ }
  return out;
}

function persistedProviders() {
  const out = [];
  try {
    for (let i = 0; i < local().length; i++) {
      const k = local().key(i);
      if (k?.startsWith(PREFIX)) out.push(k.slice(PREFIX.length));
    }
  } catch { /* ignore */ }
  return out;
}

export function getKey(providerId) {
  try {
    return session().getItem(PREFIX + providerId)
      || local().getItem(PREFIX + providerId)
      || '';
  } catch {
    return '';
  }
}

export function setKey(providerId, key) {
  try {
    if (!key) return clearKey(providerId);
    session().setItem(PREFIX + providerId, key);
    if (rememberEnabled()) local().setItem(PREFIX + providerId, key);
  } catch { /* storage unavailable */ }
}

export function clearKey(providerId) {
  try {
    session().removeItem(PREFIX + providerId);
    local().removeItem(PREFIX + providerId);
  } catch { /* ignore */ }
}

export function clearAll() {
  try {
    for (const { provider } of listSession()) clearKey(provider);
    for (const id of persistedProviders()) clearKey(id);
    local().removeItem(REMEMBER_FLAG);
  } catch { /* ignore */ }
}

/** Mask a key for display. Shows enough to recognise it and no more. */
export function mask(key) {
  if (!key) return '';
  if (key.length <= 10) return '•'.repeat(key.length);
  return key.slice(0, 6) + '•'.repeat(Math.min(12, key.length - 10)) + key.slice(-4);
}

/** A cheap sanity check so an obviously wrong paste is caught before a call. */
export function looksPlausible(provider, key) {
  if (!key) return { ok: false, why: 'No key entered.' };
  if (/\s/.test(key)) return { ok: false, why: 'The key contains whitespace.' };
  if (key.length < 16) return { ok: false, why: 'That looks too short for an API key.' };
  if (provider.keyPrefix && !key.startsWith(provider.keyPrefix)) {
    return {
      ok: true,
      why: `Most ${provider.label} keys start with “${provider.keyPrefix}”. Double-check this is the right one.`,
      warn: true,
    };
  }
  return { ok: true };
}
