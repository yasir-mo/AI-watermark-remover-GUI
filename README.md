# ScrubAI

One model scrubs off what another model signed.

Finds and removes the marks that AI tools attach to text and files: invisible
Unicode characters, C2PA provenance manifests, EXIF and XMP metadata, document
properties, and (through a rewrite pass) statistical token-sampling
watermarks.

Built for people who want their own work to be their own: no attribution
metadata riding along in a file they wrote, edited, and are publishing under
their own name. It runs entirely on your machine. Nothing is uploaded anywhere
unless you deliberately point Layer B at a remote endpoint.

The core is **Python 3.10+ standard library only**. No Flask, no FastAPI, no
pip install. Optional tools and heavy ML backends are detected at runtime and
never required.

```
┌─ Layer A ──────────── invisible Unicode ────────── lossless, deterministic ─┐
├─ Layer B ──────────── statistical watermarks ───── rewrite, probabilistic ──┤
└─ Metadata ─────────── C2PA / EXIF / XMP / props ── lossless, deterministic ─┘
                                   │
                    stdlib HTTP service on 127.0.0.1:8765
                                   │
                   ┌───────────────┴───────────────┐
              web UI at /ui/                  SKILL.md
           (drag-drop, diff, batch)     (an AI agent as the CLI)
```

## Try it without installing anything

A static build of the text layers runs at
**[yasir-mo.github.io/AI-watermark-remover-GUI](https://yasir-mo.github.io/AI-watermark-remover-GUI/)**.

There is no backend. Layer A runs in your browser, and Layer B sends your text
straight from the page to whichever provider you pick, using your own key. The
site never sees the key, because there is no server for it to reach. Open the
network tab and you will see a single request, to the provider's own domain.

| Mode | Needs | Gets you |
|---|---|---|
| Strip (Layer A) | nothing | The real cleaner, running locally in JavaScript |
| Rewrite → examples | nothing | Worked before/after pairs with live divergence scores |
| Rewrite → in-browser model | a ~500 MB download, WebGPU | A real rewrite with no key. Weak output; it shows the mechanism |
| Rewrite → your API key | a free-tier key | The real thing |

Free providers reachable from a browser: **OpenRouter** (dozens of `:free`
models through one integration), Google AI Studio, Mistral, DeepSeek, and
Hugging Face. Groq, Cerebras and Together send no CORS headers, so a browser
cannot call them. They work in the local app below, where Python makes the
request and CORS never applies.

The site is text only. Files, metadata and PDFs need the local tool.

### Layer A exists twice, on purpose

The web build ships its own JavaScript copy of Layer A so it can run with no
server. Two implementations of watermark-stripping logic could drift, and the
failure would be quiet. The site would report text as clean when the CLI
would still find marks in it.

The two are pinned together. `tools/gen_web_assets.py` generates both the
JavaScript codepoint tables and a set of conformance vectors from the Python
source, and CI runs those same vectors against both runtimes. A divergence
fails the build and blocks the deploy.

```bash
python -m tools.gen_web_assets          # regenerate after touching Layer A
python -m tools.gen_web_assets --check  # fail if stale (CI runs this)
node --test web/test/conformance.test.mjs
```

## Quick start

```bash
git clone <this repo> && cd AI-watermark-remover-GUI
python -m scrubai.server
```

Open <http://127.0.0.1:8765/ui/>.

No install step and no dependencies. To get the CLI entry points on your PATH:

```bash
pip install -e .
```

### Docker

```bash
docker compose up -d scrubai
```

The image bundles exiftool, qpdf, and c2patool, so PDFs get the full structural
rebuild. It runs as an unprivileged user with a read-only root filesystem, and
the port is published to loopback only.

## Command line

```bash
# What is hiding in this text?
python -m scrubai.cli.inspect_text article.md

# Take it out
python -m scrubai.cli.clean_text article.md -o article.clean.md --stats

# Strip a file's metadata, plus Layer A if it is a text format
python -m scrubai.cli.clean_file report.pdf --inspect
python -m scrubai.cli.clean_file report.pdf -o report.clean.pdf

# Layer B, through a local model
python -m scrubai.cli.rewrite_text draft.txt --backend ollama --model llama3.1 \
    --strength humanize --candidates 3
```

`inspect_text` exits `1` when it finds something, so it drops into a pipeline:

```bash
python -m scrubai.cli.inspect_text --json post.md | jq .findings
```

## Layer A: invisible characters

Deterministic and lossless. These characters are either in your text or they
are not, and after cleaning they are not. You can verify it by re-inspecting.

**Removed:** soft hyphen, combining grapheme joiner, Arabic letter mark,
zero-width spaces and joiners (U+200B–U+200F), bidi overrides and isolates,
word joiner and invisible operators, deprecated format characters, BOM,
variation selectors (both the BMP block and the supplementary one), tag
characters, interlinear annotation marks, and all three private-use areas.

**Normalized:** NBSP, ogham space, en/em/thin/hair spaces, narrow NBSP, medium
mathematical space, and ideographic space all become a plain U+0020.

**Aggressive mode only:** Cyrillic, Greek, and fullwidth Latin lookalikes are
folded to ASCII. Off by default, because it corrupts text that is legitimately
Cyrillic or Greek.

### It will not break your emoji

Some invisible characters do real work. The cleaner checks context before
removing anything:

| Kept | Because |
|---|---|
| ZWJ and VS15/16 after an emoji base | Family emoji, skin tones, and text-vs-emoji presentation are single graphemes |
| Tag characters after an emoji base | Subdivision flags (🏴󠁧󠁢󠁳󠁣󠁴󠁿) are built from them |
| ZWNJ/ZWJ after a non-ASCII letter | Orthographic in Persian, Arabic, Devanagari, Bengali |
| Mongolian free variation selectors | Meaningful after Mongolian letters |
| Khmer inherent vowels, Hangul jamo fillers | Meaningful inside their own clusters |
| Arabic and Syriac number signs | Always orthographic, never watermarks |

A tag character run only counts as a flag if the *run* starts after an emoji
base. Without that check, each tag character after the first would be treated
as part of a flag sequence, because its predecessor is also a tag character.
Text hidden in tag characters is removed.

Pass `--strip-emoji-glue` to remove these anyway. It will break the affected
text, which is why it is opt-in.

## Metadata

| Format | What happens |
|---|---|
| PNG | Chunks rebuilt. Only rendering chunks survive (IHDR, PLTE, IDAT, IEND, gAMA, cHRM, sRGB, iCCP, sBIT, pHYs, tRNS, bKGD, and the APNG chunks). tEXt, iTXt, zTXt, eXIf, and the C2PA `caBX` box are dropped. CRCs verified after the rebuild |
| JPEG | All APP segments and COM comments removed, including APP1 Exif/XMP, APP2 ICC, APP11 JUMBF/C2PA, APP13 IPTC. Frame headers, tables, and the entropy-coded scan are preserved byte for byte |
| WebP | EXIF, XMP, and C2PA RIFF chunks removed; the VP8X metadata flags are cleared so readers stop looking for them; the RIFF size is recomputed |
| SVG | `<metadata>`, `<title>`, `<desc>`, XMP packets, RDF blocks, `data-ai-*` attributes, and AI-related comments |
| PDF | Three tiers, described below |
| DOCX / XLSX / PPTX | `docProps/core.xml` and `app.xml` scrubbed of author, tool, company, and timeline fields; `customXml/` and C2PA parts dropped; zip timestamps normalized. The document's own title is kept |
| ODT / ODS / ODP | `meta.xml` scrubbed; `mimetype` stays first and uncompressed as ODF requires |
| HTML | `<meta name="generator">`, JSON-LD `AIGeneratedContent` blocks, `data-ai-*` attributes, AI comments. Unrelated JSON-LD is left alone |
| Markdown | AI-related YAML frontmatter keys. Your other keys survive |

### PDFs are the honest caveat

PDF is the one format where the result depends on what is installed. The tier
is always reported in `metadata.tier`.

| Tier | Requires | What you get |
|---|---|---|
| `exiftool+qpdf` | both | qpdf rewrites the file structure. The metadata bytes are **gone** |
| `exiftool` | exiftool | An incremental update. The old metadata objects usually **remain in the file**, unreferenced but recoverable |
| `stdlib` | nothing | Regex removal of XMP packets and `/Info` strings. Anything inside an object stream is **out of reach** |

Install `exiftool` and `qpdf`, or use the Docker image, if a PDF matters.

## Layer B: statistical watermarks

Some models bias their own token sampling using a secret key. Kirchenbauer's
KGW scheme hashes the previous tokens to split the vocabulary into a "green
list" and a "red list", then nudges green tokens upward before sampling.
Google's SynthID-Text runs a tournament between candidate tokens instead.
Detection recomputes the same partition and tests how green the text is.

There is nothing to delete. The mark *is* the word choices.

So Layer B rewrites the text with a second model. That model samples from its
own distribution, using its own key or none, so the original pattern does not
carry over.

```bash
python -m scrubai.cli.rewrite_text draft.md \
    --backend ollama --model mistral --strength paraphrase --candidates 3
```

Backends: `print-prompt` (the default, which calls nothing and shows you the prompt),
`ollama`, and any OpenAI-compatible `/v1/chat/completions` endpoint.

Strengths: `paraphrase`, `humanize`, `code` (rewrites comments and locals while
preserving behavior and the public API), `backtranslate` (through a pivot
language), `structural` (outline, then rewrite from the outline).

With `--candidates N` it generates several and keeps the one that diverges most
by bigram Jaccard distance, penalizing candidates whose length drifted past 2×
or below 0.5×. Afterwards it runs Layer A over the output, because rewrite
models introduce their own invisible characters.

### The tradeoff, stated plainly

Rewriting costs quality. If you are going to rewrite with a cheaper model
anyway, generating with that model in the first place is simpler. Layer B earns
its place when you specifically wanted a stronger model's reasoning and you are
willing to spend a paraphrase pass on hygiene.

If quality matters more, use the lossless path (Layer A plus metadata
stripping) and keep your original prose.

**Use a different vendor than the one that wrote the text.** Rewriting Claude
output with Claude, or Gemini output with Gemini, can re-apply the same mark.

## What this does not do

- **It is not a detection-evasion guarantee.** Layer A and metadata stripping
  are verifiable removals. Layer B displaces a watermark without proving its
  absence, and it is weaker against paragraph-level schemes like SemStamp and
  PostMark than against the token-level ones currently deployed.
- **Pixel and audio watermarks survive all of it.** Removing a C2PA manifest
  from an image does nothing to a watermark encoded in the pixels. That is what
  the optional CtrlRegen backend is for.
- **A removed mark is not a claim about origin.** The tool changes files; it
  does not change what happened.

## The HTTP service

```
GET  /health         liveness, no auth
GET  /capabilities   installed tools, backends, limits, formats
GET  /openapi.json   generated OpenAPI 3.0.3
GET  /ui/            the web frontend
POST /inspect        scan a file or text, read-only
POST /clean          strip, returns base64 bytes plus a report
POST /rewrite        Layer B
```

```bash
curl -s -X POST http://127.0.0.1:8765/inspect \
  -H 'Content-Type: application/json' \
  -d '{"text": "hello​world", "name": "note.txt"}'
```

Every response carries a `summary` reporting two separate things:
`findings_present` (there is something here to strip, and an author name counts)
and `watermark_suspected` (the stronger claim, for provenance manifests and
deliberately hidden characters). A file can be worth cleaning without being
watermarked, and the tool reports the two separately.

Binds `127.0.0.1` and requires an explicit flag to bind anything else. Set
`WATERMARKS_SERVER_API_KEY` to require a bearer token on every endpoint but
`/health`.

## The web UI

Served from `/ui/` by the same process. Plain HTML, CSS, and JavaScript, with no
build step, no CDN, and no external requests (the CSP blocks them).

- **Text**: split-pane editor, a findings table with codepoints and offsets,
  and a *reveal* view that renders every hidden character as a labelled chip
  where it actually sits, with a position ruler across the document. After
  cleaning, a character-level diff.
- **Files**: drag and drop, per-layer findings, download the stripped file,
  before-and-after comparison.
- **Batch**: several files at once, with a status table and per-file download.
- **Capabilities**: what is installed, what each tool would add, and how to
  install what is missing.

The rewrite drawer lets you pick the backend, model, endpoint, strength,
temperature, and candidate count, preview the exact prompt before anything is
sent, and accept or discard the result.

## The agent skill

`SKILL.md` teaches an AI coding agent to drive the service over curl: the agent
handles the file I/O and base64, and becomes the interface. Drop it into your
agent's skills directory. It covers reading `/capabilities` before promising
anything, inspecting before cleaning, and reporting the limits above accurately.

## Security

- Size caps on every entry point (`MAX_INPUT_BYTES`, default 256 MB)
- Binary-as-text guard: 27 magic-byte signatures, NUL detection, and a
  control-byte density check, with `--force-text` to override
- Atomic writes via `mkstemp` + `fsync` + `os.replace` in the destination
  directory, refusing to write through a symlink
- Zip guards: member count, uncompressed size, and path traversal
- Static file serving confined to `ui/`, verified against encoded traversal
- Paths starting with `-` are prefixed `./` before reaching exiftool or qpdf
- `RLIMIT_AS` and `RLIMIT_FSIZE` on child processes (POSIX)
- Layer B refuses non-HTTP schemes, refuses non-loopback endpoints without
  `--allow-remote`, and refuses to follow redirects at all, since a 3xx would
  replay the `Authorization` header against an attacker-named host
- API keys come from the environment for the CLI, never argv
- `default-src 'self'` CSP, `nosniff`, `no-referrer`

## Tests

```bash
pip install -e ".[dev]"
pytest
```

248 tests. Fixtures are generated at runtime, so no binaries live in the repo.
Coverage includes every magic-byte prefix, each load-bearing preservation rule,
the tag-run regression, atomic writes under concurrent readers, symlink
rejection, auth, and raw-socket path traversal.

## Layout

```
scrubai/
  unicode_tables.py     codepoint classification and load-bearing rules
  layer_a.py            scan, clean, diff
  rewrite.py            Layer B pipeline, backends, scoring
  safety.py             caps, guards, atomic writes, subprocess
  capabilities.py       runtime tool discovery
  server.py             the HTTP service
  openapi.py            generated spec
  metadata/             png, jpeg, webp, svg, pdf, ooxml, html_md
  cli/                  inspect_text, clean_text, clean_file,
                        rewrite_text, detect_text_watermark
ui/                     index.html, style.css, app.js
docker/                 heavy backend images
scripts/setup_markllm.sh
SKILL.md
```

## License

MIT.

MarkLLM (THU-BPM, Apache-2.0) is cloned at setup time, never vendored.
exiftool, qpdf, and c2patool are external tools under their own licenses.
