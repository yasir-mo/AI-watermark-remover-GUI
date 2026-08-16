"""CLI: strip invisible-character watermarks from text (Layer A)."""

from __future__ import annotations

import argparse
import json
import sys

from ..layer_a import clean
from ..safety import (
    BinaryInputRefused,
    InputTooLarge,
    atomic_write_text,
    ensure_text,
)
from .inspect_text import read_input


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clean-text",
        description="Remove invisible-character watermarks from text (Layer A).",
    )
    p.add_argument("path", nargs="?", default="-", help="file to clean, or - for stdin")
    p.add_argument("-o", "--output", help="write here instead of stdout (atomic)")
    p.add_argument("-i", "--in-place", action="store_true", help="overwrite the input file")
    p.add_argument("--aggressive", action="store_true", help="also fold Latin confusables")
    p.add_argument(
        "--strip-emoji-glue",
        action="store_true",
        help="do not preserve load-bearing joiners (breaks emoji and Persian/Indic text)",
    )
    p.add_argument("--normalize", action="store_true", help="apply NFKC normalization")
    p.add_argument("--force-text", action="store_true", help="process apparent binary input")
    p.add_argument(
        "--stats",
        action="store_true",
        help="print a JSON stats block to stderr",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.in_place and args.path == "-":
        print(
            "error: --in-place needs a file path; stdin cannot be edited in place",
            file=sys.stderr,
        )
        return 2
    if args.in_place and args.output:
        print("error: --in-place and --output are mutually exclusive", file=sys.stderr)
        return 2

    try:
        raw = read_input(args.path)
        text = ensure_text(raw, force=args.force_text)
    except (InputTooLarge, BinaryInputRefused, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = clean(
        text,
        aggressive=args.aggressive,
        strip_emoji_glue=args.strip_emoji_glue,
        normalize=args.normalize,
    )

    dest = args.path if args.in_place else args.output
    try:
        if dest:
            atomic_write_text(dest, result.text)
        else:
            sys.stdout.write(result.text)
    except (OSError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.stats:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
