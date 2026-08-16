"""Layer B: statistical watermark handling by paraphrase.

Why this works: KGW-style green-list watermarks and SynthID tournament sampling
both live in the *token choices* the generating model made. They are applied
token-by-token at sampling time. If a different model regenerates the prose
from its own distribution, the original bias pattern does not survive, because
the second model has no knowledge of the first model's key or green list.

Why it is not a guarantee: nothing here proves absence. Paraphrase attacks are
effective against currently deployed token-level schemes; they are weaker
against paragraph-level or semantic schemes (SemStamp, PostMark), which are not
in production deployment as of this writing. Layer B is a hygiene step. It
does not guarantee that a watermark is gone.

No model is vendored. Backends are: print-prompt (offline, prints the prompt),
ollama, and any OpenAI-compatible /v1/chat/completions endpoint.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .layer_a import clean as layer_a_clean

API_KEY_ENV = "WATERMARKS_REWRITE_API_KEY"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 180

STRENGTHS = ("paraphrase", "humanize", "code", "backtranslate", "structural")
BACKENDS = ("print-prompt", "ollama", "openai-compatible")


class RewriteError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_PROMPTS: dict[str, str] = {
    "paraphrase": """Rewrite the text below with substantially different wording at the token level.

Requirements:
- Change clause order, connectors, and transition words.
- Vary sentence boundaries and sentence length; split and merge sentences.
- Replace content words and function words wherever meaning allows.
- Preserve every fact, number, name, date, quotation, and technical identifier exactly.
- Keep the same language and the same overall length (within 20%).
- Output only the rewritten text. No preamble, no commentary, no quotation marks around it.

TEXT:
{text}""",
    "humanize": """Rewrite the text below so it reads as if a person wrote it from scratch.

Requirements:
- Vary sentence rhythm: mix short sentences with long ones.
- Replace formulaic AI transitions ("Moreover", "Furthermore", "It is important to note",
  "In conclusion", "delve", "tapestry", "landscape") with concrete natural phrasing.
- Cut hedging and filler. Prefer specific verbs over abstract nouns.
- Preserve every fact, number, name, date, and quotation exactly.
- Output only the rewritten text.

TEXT:
{text}""",
    "code": """Rewrite the natural-language parts of the code below.

Requirements:
- Rewrite comments, docstrings, and user-facing string literals in different wording.
- Rename local variables and private helpers to semantically equivalent names.
- Do NOT change program behavior, control flow, algorithms, or numeric literals.
- Do NOT rename anything in the public API: exported functions, classes, methods,
  CLI flags, config keys, or anything referenced from outside this file.
- Keep imports, formatting conventions, and language idioms intact.
- Output only the rewritten code, no fences and no commentary.

CODE:
{text}""",
    "backtranslate_forward": """Translate the text below into {pivot}.

Translate faithfully and completely. Preserve every fact, number, name, date, and
quotation. Output only the translation.

TEXT:
{text}""",
    "backtranslate_back": """Translate the {pivot} text below into {source_language}.

Translate faithfully and completely into natural, idiomatic {source_language}. Preserve
every fact, number, name, date, and quotation. Output only the translation.

TEXT:
{text}""",
    "structural_outline": """Read the text below and extract a complete bullet outline of every claim it makes.

One bullet per claim. Keep all facts, numbers, names, and dates. Do not add claims that
are not in the text. Output only the bullet list.

TEXT:
{text}""",
    "structural_expand": """Write a complete document from the outline below.

Requirements:
- Cover every bullet. Add nothing that is not in the outline.
- Write in varied, natural prose. Do not mirror the bullet order mechanically if a
  better order exists, but keep the logical structure intact.
- Match this register: {register}.
- Target roughly {target_words} words.
- Output only the document.

OUTLINE:
{text}""",
}

_PIVOT_DEFAULT = "French"


def build_prompt(text: str, strength: str, **kw: Any) -> str:
    if strength == "backtranslate":
        key = "backtranslate_forward"
    elif strength == "structural":
        key = "structural_outline"
    else:
        key = strength
    if key not in _PROMPTS:
        raise RewriteError(f"unknown strength {strength!r}; choose from {STRENGTHS}")
    return _PROMPTS[key].format(text=text, pivot=kw.get("pivot", _PIVOT_DEFAULT))


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect.

    A 3xx would cause urllib to replay the request -- including the
    Authorization header -- against whatever host the server names.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RewriteError(
            f"refusing to follow HTTP {code} redirect to {newurl!r} "
            f"(would leak the Authorization header)"
        )


_OPENER = urllib.request.build_opener(_NoRedirect)


def _check_url(url: str, allow_remote: bool) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RewriteError(f"refusing non-http(s) scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    # "0.0.0.0" here is a destination the user typed, so it counts as local.
    is_local = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")  # noqa: S104
    if not is_local and not allow_remote:
        raise RewriteError(
            f"{host!r} is not loopback; pass --allow-remote to send text off this machine"
        )


def _post_json(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    # S310: _check_url has already rejected every scheme but http/https, and
    # _OPENER refuses redirects, so the URL cannot be retargeted mid-flight.
    req = urllib.request.Request(url, data=body, method="POST")  # noqa: S310
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with _OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RewriteError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RewriteError(f"cannot reach {url}: {exc.reason}") from exc


@dataclass
class BackendConfig:
    backend: str = "print-prompt"
    model: str = "llama3.1"
    base_url: str = DEFAULT_OLLAMA_URL
    temperature: float = 0.9
    timeout: int = DEFAULT_TIMEOUT
    allow_remote: bool = False

    api_key_override: str | None = None
    """Set only from an HTTP request body (the UI's key field).

    The CLI never populates this: a key on argv is visible in ``ps`` output and
    lands in shell history. A JSON body over loopback has neither problem, so
    the UI is allowed to supply one per request. It is never logged or stored.
    """

    def api_key(self) -> str | None:
        return self.api_key_override or os.environ.get(API_KEY_ENV) or None

    def __repr__(self) -> str:  # keep keys out of tracebacks and logs
        return (
            f"BackendConfig(backend={self.backend!r}, model={self.model!r}, "
            f"base_url={self.base_url!r}, temperature={self.temperature}, "
            f"api_key={'set' if self.api_key() else 'unset'})"
        )


def call_model(prompt: str, cfg: BackendConfig) -> str:
    if cfg.backend == "print-prompt":
        return prompt
    if cfg.backend == "ollama":
        return _call_ollama(prompt, cfg)
    if cfg.backend == "openai-compatible":
        return _call_openai(prompt, cfg)
    raise RewriteError(f"unknown backend {cfg.backend!r}; choose from {BACKENDS}")


def _call_ollama(prompt: str, cfg: BackendConfig) -> str:
    url = cfg.base_url.rstrip("/") + "/api/chat"
    _check_url(url, cfg.allow_remote)
    payload = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": cfg.temperature},
    }
    data = _post_json(url, payload, {}, cfg.timeout)
    content = (data.get("message") or {}).get("content")
    if not content:
        raise RewriteError(f"ollama returned no content: {str(data)[:300]}")
    return content.strip()


def _call_openai(prompt: str, cfg: BackendConfig) -> str:
    url = cfg.base_url.rstrip("/") + "/v1/chat/completions"
    _check_url(url, cfg.allow_remote)
    headers: dict[str, str] = {}
    key = cfg.api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": cfg.temperature,
    }
    data = _post_json(url, payload, headers, cfg.timeout)
    choices = data.get("choices") or []
    if not choices:
        raise RewriteError(f"endpoint returned no choices: {str(data)[:300]}")
    return (choices[0].get("message") or {}).get("content", "").strip()


# --------------------------------------------------------------------------
# Divergence scoring
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _bigrams(text: str) -> set[tuple[str, str]]:
    words = [w.lower() for w in _WORD_RE.findall(text)]
    return set(itertools.pairwise(words))


def divergence(original: str, candidate: str) -> float:
    """1 - Jaccard similarity over word bigrams. Higher means more diverged."""
    a, b = _bigrams(original), _bigrams(candidate)
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return 1.0 - (len(a & b) / len(union))


def score_candidate(original: str, candidate: str) -> tuple[float, list[str]]:
    """Divergence with a penalty for extreme length drift."""
    notes: list[str] = []
    score = divergence(original, candidate)
    if original:
        ratio = len(candidate) / len(original)
        if ratio > 2.0 or ratio < 0.5:
            score -= 0.15
            notes.append(f"length drift penalty applied (ratio {ratio:.2f})")
    return score, notes


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    text: str
    score: float
    divergence: float
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": round(self.score, 4),
            "divergence": round(self.divergence, 4),
            "notes": self.notes,
        }


@dataclass
class RewriteResult:
    original: str
    text: str
    strength: str
    backend: str
    model: str
    candidates: list[Candidate]
    prompt: str
    layer_a_after: dict[str, Any] | None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "B",
            "strength": self.strength,
            "backend": self.backend,
            "model": self.model,
            "input_length": len(self.original),
            "output_length": len(self.text),
            "divergence": round(divergence(self.original, self.text), 4),
            "text": self.text,
            "prompt": self.prompt,
            "candidates": [c.to_dict() for c in self.candidates],
            "layer_a_after": self.layer_a_after,
            "notes": self.notes,
        }


def rewrite(
    text: str,
    *,
    strength: str = "paraphrase",
    cfg: BackendConfig | None = None,
    candidates: int = 1,
    layer_a_after: bool = True,
    pivot: str = _PIVOT_DEFAULT,
    source_language: str = "English",
    register: str = "the same register as the original",
    progress: Callable[[str], None] | None = None,
) -> RewriteResult:
    """Run the rewrite pipeline and return the most-diverged candidate."""
    cfg = cfg or BackendConfig()
    if strength not in STRENGTHS:
        raise RewriteError(f"unknown strength {strength!r}; choose from {STRENGTHS}")
    if not 1 <= candidates <= 5:
        raise RewriteError("candidates must be between 1 and 5")

    notes: list[str] = []
    say = progress or (lambda _m: None)

    prompt = build_prompt(text, strength, pivot=pivot)

    def one_pass() -> str:
        if strength == "backtranslate":
            say(f"backtranslate: {source_language} -> {pivot}")
            mid = call_model(
                _PROMPTS["backtranslate_forward"].format(text=text, pivot=pivot), cfg
            )
            if cfg.backend == "print-prompt":
                return mid
            say(f"backtranslate: {pivot} -> {source_language}")
            return call_model(
                _PROMPTS["backtranslate_back"].format(
                    text=mid, pivot=pivot, source_language=source_language
                ),
                cfg,
            )
        if strength == "structural":
            say("structural: extracting outline")
            outline = call_model(_PROMPTS["structural_outline"].format(text=text), cfg)
            if cfg.backend == "print-prompt":
                return outline
            say("structural: expanding outline")
            return call_model(
                _PROMPTS["structural_expand"].format(
                    text=outline,
                    register=register,
                    target_words=max(50, len(_WORD_RE.findall(text))),
                ),
                cfg,
            )
        return call_model(prompt, cfg)

    scored: list[Candidate] = []
    for i in range(candidates):
        say(f"candidate {i + 1}/{candidates}")
        out = one_pass()
        score, cnotes = score_candidate(text, out)
        scored.append(Candidate(out, score, divergence(text, out), cnotes))

    scored.sort(key=lambda c: c.score, reverse=True)
    best = scored[0].text

    layer_a_report: dict[str, Any] | None = None
    if layer_a_after and cfg.backend != "print-prompt":
        cleaned = layer_a_clean(best)
        best = cleaned.text
        layer_a_report = cleaned.to_dict()
        if cleaned.total_removed or cleaned.total_replaced:
            notes.append(
                "the rewrite model introduced its own invisible characters; "
                "Layer A cleaned them from the output"
            )

    if cfg.backend == "print-prompt":
        notes.append(
            "print-prompt backend: no model was called, the output is the prompt itself"
        )

    notes.append(
        "cross-vendor hygiene: rewrite with a model from a different vendor than "
        "the one that produced the text. Rewriting with the origin model can "
        "re-apply the same watermark key."
    )

    return RewriteResult(
        original=text,
        text=best,
        strength=strength,
        backend=cfg.backend,
        model=cfg.model,
        candidates=scored,
        prompt=prompt,
        layer_a_after=layer_a_report,
        notes=notes,
    )


#: Titles and abbreviations whose trailing period does not end a sentence.
_ABBREVIATIONS = (
    "Mr",
    "Mrs",
    "Ms",
    "Dr",
    "Prof",
    "Sr",
    "Jr",
    "St",
    "Rev",
    "Hon",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "cf",
    "al",
    "Inc",
    "Ltd",
    "Co",
    "Corp",
    "Fig",
    "No",
    "Vol",
    "pp",
    "approx",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "Jun",
    "Jul",
    "Aug",
    "Sept",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_SENTENCE_RE = re.compile(
    r"(?<=[.!?])[ \t]+(?=[\"'(\[]*[A-Z0-9])",
)


def split_sentences(text: str) -> list[str]:
    """Rough sentence splitter used for chunking long inputs.

    Not a full NLP tokenizer -- it only needs to avoid cutting mid-sentence,
    since the chunks are fed to a paraphrasing model. Common titles and
    abbreviations are protected so "Dr. Chen" stays one sentence.
    """
    guard = re.compile(
        r"\b(" + "|".join(re.escape(a) for a in _ABBREVIATIONS) + r")\.",
    )
    # Temporarily mask the abbreviation periods so the splitter ignores them.
    masked = guard.sub(lambda m: m.group(1) + "\x00", text)
    parts = [p.replace("\x00", ".") for p in _SENTENCE_RE.split(masked)]
    return [p for p in parts if p.strip()]


def chunk_text(text: str, max_chars: int = 6000) -> list[str]:
    """Group sentences into chunks that fit a model context comfortably."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        if size + len(para) > max_chars and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        current.append(para)
        size += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks
