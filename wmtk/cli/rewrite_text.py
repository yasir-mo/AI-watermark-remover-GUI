"""CLI: Layer B rewrite pipeline."""

from __future__ import annotations

import argparse
import json
import sys

from ..rewrite import (
    BACKENDS,
    DEFAULT_OLLAMA_URL,
    STRENGTHS,
    BackendConfig,
    RewriteError,
    chunk_text,
    rewrite,
)
from ..safety import (
    BinaryInputRefused,
    InputTooLarge,
    atomic_write_text,
    ensure_text,
)
from .inspect_text import read_input


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rewrite-text",
        description=(
            "Rewrite text through a second model so the original model's "
            "token-sampling watermark does not survive (Layer B)."
        ),
        epilog=(
            "API keys are read from WATERMARKS_REWRITE_API_KEY only, never from "
            "the command line, because argv is visible in ps and shell history."
        ),
    )
    p.add_argument("path", nargs="?", default="-", help="file to rewrite, or - for stdin")
    p.add_argument("-o", "--output", help="write here instead of stdout (atomic)")
    p.add_argument("--backend", choices=BACKENDS, default="print-prompt")
    p.add_argument("--model", default="llama3.1")
    p.add_argument("--base-url", default=DEFAULT_OLLAMA_URL)
    p.add_argument("--strength", choices=STRENGTHS, default="paraphrase")
    p.add_argument("--temperature", type=float, default=0.9)
    p.add_argument(
        "--candidates", type=int, default=1, help="generate N, keep the most diverged (1-5)"
    )
    p.add_argument(
        "--pivot", default="French", help="pivot language for --strength backtranslate"
    )
    p.add_argument("--source-language", default="English")
    p.add_argument(
        "--chunk-chars", type=int, default=6000, help="split inputs longer than this"
    )
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument(
        "--allow-remote",
        action="store_true",
        help="permit a non-loopback endpoint (your text leaves this machine)",
    )
    p.add_argument(
        "--no-layer-a-after",
        action="store_true",
        help="skip the Layer A pass over the rewritten output",
    )
    p.add_argument("--json", action="store_true", help="emit the full result as JSON")
    p.add_argument("--quiet", action="store_true", help="suppress progress on stderr")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        text = ensure_text(read_input(args.path))
    except (InputTooLarge, BinaryInputRefused, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cfg = BackendConfig(
        backend=args.backend,
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        timeout=args.timeout,
        allow_remote=args.allow_remote,
    )

    def progress(msg: str) -> None:
        if not args.quiet:
            print(f"[rewrite] {msg}", file=sys.stderr)

    chunks = chunk_text(text, args.chunk_chars)
    if len(chunks) > 1:
        progress(f"input split into {len(chunks)} chunk(s)")

    outputs: list[str] = []
    reports: list[dict] = []
    try:
        for idx, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                progress(f"chunk {idx}/{len(chunks)}")
            result = rewrite(
                chunk,
                strength=args.strength,
                cfg=cfg,
                candidates=args.candidates,
                layer_a_after=not args.no_layer_a_after,
                pivot=args.pivot,
                source_language=args.source_language,
                progress=progress,
            )
            outputs.append(result.text)
            reports.append(result.to_dict())
    except RewriteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    final = "\n\n".join(outputs)

    if args.json:
        payload = {
            "chunks": reports,
            "text": final,
            "backend": args.backend,
            "strength": args.strength,
        }
        out_text = json.dumps(payload, indent=2, ensure_ascii=False)
    else:
        out_text = final

    if args.output:
        atomic_write_text(args.output, out_text)
    else:
        sys.stdout.write(out_text)
        if not out_text.endswith("\n"):
            sys.stdout.write("\n")

    if not args.quiet and not args.json:
        for note in reports[0]["notes"]:
            print(f"[note] {note}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
