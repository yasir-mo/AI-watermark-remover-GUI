---
name: watermark-removal
description: Use when the user wants to find or remove AI watermarks from text or files - invisible Unicode characters, C2PA/Content Credentials manifests, EXIF/XMP metadata, document properties, or statistical token-sampling watermarks. Talks to a local wmtk HTTP service via curl.
---

# Watermark removal

You are the interface to a local service that finds and strips watermarks. The
user has no CLI to run; you do the file I/O and the HTTP calls, and you report
what was actually found and removed.

The service listens on `http://127.0.0.1:8765` by default. Everything stays on
this machine unless the user explicitly configures a remote rewrite backend.

## Three layers

| Layer | What it removes | Reversible? | Confidence |
|---|---|---|---|
| **A** | Invisible Unicode: zero-width spaces, joiners, bidi controls, tag characters, variation selectors, private-use codepoints, space homoglyphs | Lossless. Deterministic. | High — the characters are either there or not |
| **B** | Statistical token-sampling watermarks (KGW green lists, SynthID tournament sampling) | Rewrites the prose. Not lossless. | Best-effort. See the limits section |
| **Metadata** | C2PA manifests, EXIF, XMP, IPTC, document properties, AI frontmatter and generator tags | Lossless for the content. Deterministic. | High, with a caveat for PDFs |

## Start here, every time

Check the service is up and see what it can do:

```bash
curl -s http://127.0.0.1:8765/health
curl -s http://127.0.0.1:8765/capabilities
```

`/health` returns `{"ok": true, "version": "..."}`. If curl cannot connect, the
service is not running — tell the user to start it:

```bash
python -m wmtk.server
```

`/capabilities` tells you which optional tools are installed. Read it before
promising anything:

- `tools.exiftool.installed` — richer metadata reporting and a second scrub pass
- `tools.c2patool.installed` — can actually read and verify C2PA manifests
- `tools.qpdf.installed` — PDFs get a structural rebuild instead of a patch
- `pdf_tier` — `exiftool+qpdf` (best), `exiftool` (partial), or `stdlib` (degraded)

If a tool is missing, the response carries an `install` string for it. Pass that
along rather than inventing install steps.

### Bearer token

If the operator set `WATERMARKS_SERVER_API_KEY`, every endpoint except `/health`
needs a header. Add it to all calls:

```bash
curl -s -H "Authorization: Bearer $WATERMARKS_SERVER_API_KEY" \
     http://127.0.0.1:8765/capabilities
```

A `401` response means the token is missing or wrong. Ask the user for it; do
not guess.

## Always inspect before you clean

Inspect is read-only. Run it first so you can tell the user what is there before
you change their file.

### Text

```bash
curl -s -X POST http://127.0.0.1:8765/inspect \
  -H 'Content-Type: application/json' \
  -d '{"text": "the text to scan", "name": "note.txt"}'
```

For text with quotes or newlines, write it to a file and build the JSON with a
tool rather than hand-escaping it:

```bash
python3 -c 'import json,sys; print(json.dumps({"text": open(sys.argv[1], encoding="utf-8").read(), "name": sys.argv[1]}))' input.txt > /tmp/req.json
curl -s -X POST http://127.0.0.1:8765/inspect \
  -H 'Content-Type: application/json' --data-binary @/tmp/req.json
```

### Files

Base64-encode the file into the `file` field. `-w0` keeps it on one line:

```bash
python3 -c 'import base64,json,sys; p=sys.argv[1]; print(json.dumps({"file": base64.b64encode(open(p,"rb").read()).decode(), "name": p.split("/")[-1]}))' photo.jpg > /tmp/req.json
curl -s -X POST http://127.0.0.1:8765/inspect \
  -H 'Content-Type: application/json' --data-binary @/tmp/req.json
```

### Reading the response

```json
{
  "name": "photo.jpg",
  "format": "jpeg",
  "bytes": 184320,
  "is_text_format": false,
  "metadata": {
    "tier": "stdlib+exiftool",
    "count": 14,
    "c2pa_present": true,
    "by_kind": {"exif": 9, "c2pa": 2, "xmp": 3},
    "findings": [{"kind": "c2pa", "detail": "...", "confidence": "confirmed"}],
    "warnings": []
  },
  "layer_a": {
    "total_flagged": 7,
    "total_removable": 5,
    "findings": [{"codepoint": "U+200B", "name": "ZERO WIDTH SPACE",
                  "kind": "zwj_family", "count": 3, "offsets": [12, 44, 91],
                  "kept_as_load_bearing": 0}]
  },
  "summary": {"findings_present": true, "watermark_suspected": true,
              "c2pa_present": true, "metadata_findings": 14, "invisible_chars": 5}
}
```

Report `summary` first, then the specifics. Three fields deserve care:

- **`findings_present` vs `watermark_suspected`** — these are different claims.
  `findings_present` means there is something to strip; an author name in a
  DOCX counts. `watermark_suspected` is the stronger claim, reserved for
  provenance manifests and deliberately hidden characters. A file can have
  plenty worth removing and still not be watermarked. Say which one you mean.

- **`kept_as_load_bearing`** — characters the cleaner will deliberately keep.
  A zero-width joiner between two emoji, or between Persian letters, is doing
  real typographic work. `total_flagged` minus `total_removable` is what stays.
  Do not describe these as "not removed yet"; they are correctly preserved.
- **`metadata.warnings`** — say these out loud. The PDF degraded tier warns that
  it cannot reach metadata inside object streams. That limitation is real.

## Clean

Same request shape; the response adds the cleaned bytes.

```bash
curl -s -X POST http://127.0.0.1:8765/clean \
  -H 'Content-Type: application/json' --data-binary @/tmp/req.json > /tmp/resp.json
```

Write the result back out. Never write over the original unless the user asked
you to:

```bash
python3 -c 'import base64,json,sys; d=json.load(open("/tmp/resp.json")); open(sys.argv[1],"wb").write(base64.b64decode(d["cleaned"]))' photo.cleaned.jpg
```

Then report `report.bytes_removed`, `report.metadata.actions`, and
`report.layer_a.total_removed`.

### Options

Pass an `options` object on either endpoint:

| Option | Default | Effect |
|---|---|---|
| `aggressive` | `false` | Also fold Cyrillic/Greek/fullwidth lookalikes to ASCII. Only use when the user suspects homoglyph substitution — it corrupts genuinely non-Latin text |
| `strip_emoji_glue` | `false` | Remove load-bearing joiners too. **This breaks emoji sequences and Persian, Devanagari, and Khmer text.** Only on explicit request |
| `normalize` | `false` | NFKC-normalize after cleaning |
| `layer_a` | `true` | Scan/clean invisible characters (text formats only) |
| `metadata` | `true` | Scan/clean file metadata |
| `use_tools` | `true` | Consult exiftool and c2patool |
| `diff` | `false` | Include a character-level diff in the clean report |

### Verify

Cleaning is cheap to check. Re-inspect the output and confirm it comes back
clean before you tell the user it worked:

```bash
curl -s -X POST http://127.0.0.1:8765/inspect \
  -H 'Content-Type: application/json' \
  -d "{\"file\": $(jq .cleaned /tmp/resp.json), \"name\": \"photo.jpg\"}"
```

## Layer B: rewriting

Only reach for this when the user is worried about *statistical* watermarks —
the token-choice patterns a model bakes in while generating. Layer A cannot
touch those, because there is nothing invisible to delete.

```bash
curl -s -X POST http://127.0.0.1:8765/rewrite \
  -H 'Content-Type: application/json' \
  -d '{"text": "...", "backend": "ollama", "model": "llama3.1",
       "strength": "paraphrase", "candidates": 3}'
```

Backends: `print-prompt` (default, calls nothing — returns the prompt so the
user can run it themselves), `ollama`, `openai-compatible`.

Strengths: `paraphrase`, `humanize`, `code`, `backtranslate`, `structural`.

Three rules:

1. **Use a different vendor.** If the text came from Claude, rewrite with a
   local Ollama model. If it came from Gemini, do not rewrite with Gemini.
   Rewriting with the origin model can re-stamp the same watermark.
2. **Loopback only, unless the user says otherwise.** A non-local `base_url`
   is refused unless `allow_remote: true`. Sending it means the user's text
   leaves their machine — confirm before you set that flag.
3. **Check the divergence.** The response has `divergence` (0 to 1). Below
   roughly 0.5 the rewrite barely changed the wording, and the original token
   pattern may partly survive. Raise `temperature` or `candidates` and retry.

Show the user the rewritten text before treating it as final. It is their prose;
a paraphrase is a real edit, not a cleanup.

## What to tell the user about limits

Be accurate about this. Overstating it is the main way this tool misleads.

- **Layer A is exact.** Those characters are gone. You can verify it.
- **Metadata stripping is exact** for PNG, JPEG, WebP, SVG, DOCX, ODT, HTML,
  and Markdown — the bytes are rebuilt without them.
- **PDFs depend on the tier.** With qpdf, the file is structurally rebuilt and
  the metadata is genuinely gone. Without it, old metadata objects can survive
  in the file unreferenced. Report `metadata.tier` when it matters.
- **Layer B is probabilistic.** A rewrite displaces token-level watermarks
  because the second model samples from its own distribution. It is not a proof
  of absence, and it is weaker against paragraph-level schemes. Never say text
  is "undetectable".
- **Pixel and audio watermarks survive all of this.** Stripping a C2PA manifest
  from an image does not remove a watermark encoded in the pixels themselves.
  That needs the optional CtrlRegen backend.

## Endpoint reference

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness. No auth |
| GET | `/capabilities` | Installed tools, backends, limits, formats |
| GET | `/openapi.json` | Full generated spec |
| POST | `/inspect` | Scan. Read-only |
| POST | `/clean` | Strip, returns base64 bytes plus a report |
| POST | `/rewrite` | Layer B paraphrase |

Formats: PNG, JPEG, WebP, SVG, PDF, DOCX, XLSX, PPTX, ODT, HTML, Markdown, text.

## Troubleshooting

| Symptom | Cause | Do this |
|---|---|---|
| `Connection refused` | Service is down | `python -m wmtk.server` |
| `401` | Bearer token needed | Ask the user for `WATERMARKS_SERVER_API_KEY` |
| `413` | Over the size cap | Check `limits.max_input_bytes` in `/capabilities` |
| `400 file is not valid base64` | Line wrapping | Use `base64 -w0`, or the Python one-liner above |
| `502` on `/rewrite` | Backend unreachable | Confirm Ollama is running on the `base_url` |
| `metadata.count` is 0 but the user expects more | Optional tools missing | Check `/capabilities`, share the `install` string |
| PDF still shows metadata | `stdlib` tier | Install qpdf and exiftool, re-run |
