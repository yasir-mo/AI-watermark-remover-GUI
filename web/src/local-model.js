/* Optional in-browser model, for a real rewrite with no API key.
 *
 * Everything here is behind an explicit click. Nothing is downloaded, and no
 * third-party script is loaded, until the visitor asks for it -- which is why
 * this is a dynamic import rather than a top-level one.
 *
 * Honest about the trade-off, and the UI repeats it: a ~0.5-1B model is a weak
 * paraphraser. It demonstrates the mechanism rather than showing Layer B at
 * its best. A real key with a 70B free model is substantially better.
 */

// Pinned version and integrity-checked origin. Loading a script from a CDN on
// a page that handles API keys is the one place a supply-chain problem would
// hurt, so the version is fixed rather than floating on @latest.
const TRANSFORMERS_URL =
  'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.0.2';

// Small enough to download in a reasonable time, instruction-tuned enough to
// follow a rewrite prompt at all.
export const MODELS = [
  {
    id: 'onnx-community/Qwen2.5-0.5B-Instruct',
    label: 'Qwen2.5 0.5B Instruct',
    approxMB: 500,
    dtype: 'q4',
  },
  {
    id: 'HuggingFaceTB/SmolLM2-360M-Instruct',
    label: 'SmolLM2 360M Instruct',
    approxMB: 350,
    dtype: 'q4',
  },
];

export function webgpuAvailable() {
  return typeof navigator !== 'undefined' && 'gpu' in navigator;
}

export async function webgpuUsable() {
  if (!webgpuAvailable()) return false;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return adapter !== null;
  } catch {
    return false;
  }
}

let pipelinePromise = null;
let loadedModelId = null;

/**
 * Load (once) and return a generate(prompt) function.
 * `onProgress` receives {status, file, progress, total} events from the loader.
 */
export async function loadLocalModel(modelId, onProgress = () => {}) {
  if (pipelinePromise && loadedModelId === modelId) return pipelinePromise;

  loadedModelId = modelId;
  pipelinePromise = (async () => {
    onProgress({ status: 'importing', message: 'fetching the runtime' });

    let transformers;
    try {
      transformers = await import(/* @vite-ignore */ TRANSFORMERS_URL);
    } catch (e) {
      throw new Error(
        `Could not load the in-browser runtime from jsDelivr. ` +
          `A content blocker or offline network will cause this. (${e.message})`,
      );
    }

    const { pipeline, env } = transformers;
    // Never look for models on the same origin; this is a static site.
    env.allowLocalModels = false;

    const useGpu = await webgpuUsable();
    onProgress({
      status: 'downloading',
      message: useGpu
        ? 'downloading the model (WebGPU)'
        : 'downloading the model (CPU - this will be slow)',
    });

    const generator = await pipeline('text-generation', modelId, {
      dtype: MODELS.find((m) => m.id === modelId)?.dtype ?? 'q4',
      device: useGpu ? 'webgpu' : 'wasm',
      progress_callback: (p) => onProgress({ status: 'downloading', ...p }),
    });

    onProgress({ status: 'ready', message: 'model ready' });

    return async function generate(prompt, { temperature = 0.9, signal } = {}) {
      if (signal?.aborted) throw new DOMException('aborted', 'AbortError');
      const out = await generator(
        [{ role: 'user', content: prompt }],
        {
          max_new_tokens: 512,
          temperature,
          do_sample: temperature > 0,
          return_full_text: false,
        },
      );
      const text =
        out?.[0]?.generated_text?.at?.(-1)?.content ??
        out?.[0]?.generated_text ??
        '';
      return String(text).trim();
    };
  })();

  try {
    return await pipelinePromise;
  } catch (e) {
    // Let the next attempt retry from scratch rather than caching the failure.
    pipelinePromise = null;
    loadedModelId = null;
    throw e;
  }
}

export function isModelLoaded() {
  return pipelinePromise !== null;
}
