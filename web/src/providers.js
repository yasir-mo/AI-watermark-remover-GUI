/* Provider registry.
 *
 * Adding a provider is a data edit.
 *
 * `browserDirect` is the field that matters for the hosted demo. The static
 * site calls providers straight from the page with the visitor's own key, so a
 * provider is only usable here if it sends CORS headers. Groq, Cerebras and
 * Together do not (verified by preflight, 2026-08-15) -- they work fine in the
 * local Python app, where the server makes the call and CORS never applies.
 *
 * Keys never reach a server belonging to this project. There is no server.
 */

export const PROVIDERS = [
  {
    id: 'openrouter',
    label: 'OpenRouter',
    browserDirect: true,
    endpoint: 'https://openrouter.ai/api/v1/chat/completions',
    modelsUrl: 'https://openrouter.ai/api/v1/models',
    keyUrl: 'https://openrouter.ai/keys',
    keyPrefix: 'sk-or-',
    // One integration, dozens of free models. The best single answer to
    // "support many APIs, especially free ones".
    defaultModel: 'meta-llama/llama-3.3-70b-instruct:free',
    fallbackModels: [
      'meta-llama/llama-3.3-70b-instruct:free',
      'google/gemma-2-9b-it:free',
      'mistralai/mistral-7b-instruct:free',
      'qwen/qwen-2.5-72b-instruct:free',
    ],
    freeNote:
      'Aggregates many models. Anything with a “:free” suffix costs nothing.',
    // OpenRouter asks callers to identify themselves; harmless and public.
    extraHeaders: () => ({
      // Guarded: this module is also imported by the Node test runner,
      // where `location` does not exist.
      'HTTP-Referer': typeof location === 'undefined' ? '' : location.origin,
      'X-Title': 'wmtk Layer B',
    }),
    // Free models are discoverable at runtime.
    filterModels: (list) =>
      list
        .filter((m) => m.id.endsWith(':free') || Number(m.pricing?.prompt) === 0)
        .map((m) => m.id)
        .sort(),
  },
  {
    id: 'google',
    label: 'Google AI Studio',
    browserDirect: true,
    endpoint:
      'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
    modelsUrl: null,
    keyUrl: 'https://aistudio.google.com/apikey',
    keyPrefix: 'AIza',
    defaultModel: 'gemini-2.0-flash',
    fallbackModels: ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-flash'],
    freeNote: 'Generous free tier. Note the non-standard /v1beta/openai path.',
  },
  {
    id: 'mistral',
    label: 'Mistral',
    browserDirect: true,
    endpoint: 'https://api.mistral.ai/v1/chat/completions',
    modelsUrl: 'https://api.mistral.ai/v1/models',
    keyUrl: 'https://console.mistral.ai/api-keys',
    defaultModel: 'mistral-small-latest',
    fallbackModels: ['mistral-small-latest', 'open-mistral-nemo', 'ministral-8b-latest'],
    freeNote: 'Free experiment tier on la Plateforme.',
    filterModels: (list) => list.map((m) => m.id).sort(),
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    browserDirect: true,
    endpoint: 'https://api.deepseek.com/v1/chat/completions',
    modelsUrl: 'https://api.deepseek.com/v1/models',
    keyUrl: 'https://platform.deepseek.com/api_keys',
    keyPrefix: 'sk-',
    defaultModel: 'deepseek-chat',
    fallbackModels: ['deepseek-chat', 'deepseek-reasoner'],
    freeNote: 'Very low cost; trial credit on signup.',
    filterModels: (list) => list.map((m) => m.id).sort(),
  },
  {
    id: 'huggingface',
    label: 'Hugging Face',
    browserDirect: true,
    endpoint: 'https://router.huggingface.co/v1/chat/completions',
    modelsUrl: 'https://router.huggingface.co/v1/models',
    keyUrl: 'https://huggingface.co/settings/tokens',
    keyPrefix: 'hf_',
    defaultModel: 'meta-llama/Llama-3.1-8B-Instruct',
    fallbackModels: [
      'meta-llama/Llama-3.1-8B-Instruct',
      'Qwen/Qwen2.5-7B-Instruct',
      'mistralai/Mistral-7B-Instruct-v0.3',
    ],
    freeNote: 'Monthly free inference credits on a free account.',
    filterModels: (list) => list.map((m) => m.id).sort().slice(0, 200),
  },
  {
    id: 'openai-compatible',
    label: 'Custom OpenAI-compatible',
    browserDirect: true,
    endpoint: null, // supplied by the user
    editableEndpoint: true,
    modelsUrl: null,
    keyUrl: null,
    defaultModel: '',
    fallbackModels: [],
    freeNote:
      'Point at any OpenAI-compatible endpoint, including one running on your own machine.',
  },

  /* ── Not reachable from a browser ──────────────────────────────────────
   * These send no CORS headers, so the page cannot call them. They are listed
   * so the UI can explain why, and point at the local app where they do work.
   */
  {
    id: 'groq',
    label: 'Groq',
    browserDirect: false,
    endpoint: 'https://api.groq.com/openai/v1/chat/completions',
    keyUrl: 'https://console.groq.com/keys',
    defaultModel: 'llama-3.3-70b-versatile',
    freeNote: 'Fast and free, but blocks browser calls. Use the local app.',
  },
  {
    id: 'cerebras',
    label: 'Cerebras',
    browserDirect: false,
    endpoint: 'https://api.cerebras.ai/v1/chat/completions',
    keyUrl: 'https://cloud.cerebras.ai/',
    defaultModel: 'llama3.1-8b',
    freeNote: 'Free tier, but blocks browser calls. Use the local app.',
  },
  {
    id: 'together',
    label: 'Together',
    browserDirect: false,
    endpoint: 'https://api.together.xyz/v1/chat/completions',
    keyUrl: 'https://api.together.ai/settings/api-keys',
    defaultModel: 'meta-llama/Llama-3.3-70B-Instruct-Turbo-Free',
    freeNote: 'Has free models, but blocks browser calls. Use the local app.',
  },
];

export const byId = (id) => PROVIDERS.find((p) => p.id === id);
export const browserProviders = () => PROVIDERS.filter((p) => p.browserDirect);
export const blockedProviders = () => PROVIDERS.filter((p) => !p.browserDirect);

/** Fetch the live model list for a provider, falling back to the static list. */
export async function listModels(provider, key) {
  if (!provider.modelsUrl || !key) return provider.fallbackModels || [];
  try {
    const resp = await fetch(provider.modelsUrl, {
      headers: { Authorization: `Bearer ${key}` },
    });
    if (!resp.ok) return provider.fallbackModels || [];
    const body = await resp.json();
    const list = body.data || body.models || [];
    const picked = provider.filterModels
      ? provider.filterModels(list)
      : list.map((m) => m.id);
    return picked.length ? picked : provider.fallbackModels || [];
  } catch {
    return provider.fallbackModels || [];
  }
}
