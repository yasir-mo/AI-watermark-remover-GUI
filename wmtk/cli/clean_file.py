"""CLI: inspect and strip file metadata (C2PA, EXIF, XMP, document properties)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..layer_a import clean as layer_a_clean
from ..layer_a import scan as layer_a_scan
from ..metadata import TEXT_FORMATS, clean_file, detect_format, inspect_file
from ..safety import InputTooLarge, atomic_write_bytes, check_size


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clean-file",
        description="Inspect or strip metadata from a file, plus Layer A for text formats.",
    )
    p.add_argument("path", help="file to process")
    p.add_argument("--inspect", action="store_true", help="report only, do not write anything")
    p.add_argument("-o", "--output", help="write here (default: <name>.cleaned<ext>)")
    p.add_argument("-i", "--in-place", action="store_true", help="overwrite the input")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-tools", action="store_true", help="skip exiftool/c2patool enrichment")
    p.add_argument(
        "--no-layer-a",
        action="store_true",
        help="skip invisible-character cleaning on text formats",
    )
    p.add_argument("--aggressive", action="store_true", help="Layer A aggressive mode")
    return p


def _default_output(path: Path) -> Path:
    return path.with_name(f"{path.stem}.cleaned{path.suffix}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    src = Path(args.path)

    try:
        data = src.read_bytes()
        check_size(data)
    except (OSError, InputTooLarge) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fmt = detect_format(src.name, data)
    report: dict = {"file": str(src), "format": fmt, "bytes": len(data)}

    if args.inspect:
        meta = inspect_file(data, src.name, use_tools=not args.no_tools)
        report["metadata"] = meta.to_dict()
        if fmt in TEXT_FORMATS and not args.no_layer_a:
            text = data.decode("utf-8", "surrogateescape")
            report["layer_a"] = layer_a_scan(text, aggressive=args.aggressive).to_dict()
        _emit(report, args.json)
        return 0

    out, meta = clean_file(data, src.name)
    report["metadata"] = meta.to_dict()

    if fmt in TEXT_FORMATS and not args.no_layer_a:
        text = out.decode("utf-8", "surrogateescape")
        cleaned = layer_a_clean(text, aggressive=args.aggressive)
        out = cleaned.text.encode("utf-8", "surrogateescape")
        report["layer_a"] = cleaned.to_dict()

    dest = (
        Path(args.output) if args.output else (src if args.in_place else _default_output(src))
    )
    try:
        atomic_write_bytes(dest, out)
    except (OSError, PermissionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report["output"] = str(dest)
    report["output_bytes"] = len(out)
    report["bytes_saved"] = len(data) - len(out)
    _emit(report, args.json)
    return 0


def _emit(report: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    meta = report["metadata"]
    print(f"{report['file']}  [{report['format']}, {report['bytes']} bytes]")
    print(f"  handler tier: {meta['tier']}")
    print(
        f"  metadata findings: {meta['count']}"
        + ("  (C2PA manifest present)" if meta["c2pa_present"] else "")
    )
    for f in meta["findings"][:40]:
        print(f"    [{f['kind']}] {f['detail']}")
    if meta["count"] > 40:
        print(f"    ... and {meta['count'] - 40} more")

    if "layer_a" in report:
        la = report["layer_a"]
        if "total_removable" in la:
            print(
                f"  layer A: {la['total_flagged']} flagged, {la['total_removable']} removable"
            )
        else:
            print(f"  layer A: removed {la['total_removed']}, replaced {la['total_replaced']}")

    for action in meta["actions"]:
        print(f"  action: {action}")
    for warning in meta["warnings"]:
        print(f"  warning: {warning}")
    if "output" in report:
        print(
            f"  wrote {report['output']} ({report['output_bytes']} bytes, "
            f"{report['bytes_saved']:+d})"
        )


if __name__ == "__main__":
    raise SystemExit(main())
