"""CLI: scan text for invisible-character watermarks (Layer A)."""

from __future__ import annotations

import argparse
import json
import sys

from ..layer_a import scan
from ..safety import BinaryInputRefused, InputTooLarge, ensure_text


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inspect-text",
        description="Report suspicious Unicode characters in text (Layer A).",
    )
    p.add_argument("path", nargs="?", default="-", help="file to scan, or - for stdin")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument(
        "--aggressive",
        action="store_true",
        help="also flag Cyrillic/Greek/fullwidth Latin confusables",
    )
    p.add_argument(
        "--strip-emoji-glue",
        action="store_true",
        help="do not preserve load-bearing joiners (breaks emoji and Persian/Indic text)",
    )
    p.add_argument(
        "--force-text",
        action="store_true",
        help="process input even if it looks like binary data",
    )
    return p


def read_input(path: str) -> bytes:
    if path == "-":
        return sys.stdin.buffer.read()
    with open(path, "rb") as fh:
        return fh.read()


def render_human(report: dict, source: str) -> str:
    lines = [f"{source}: {report['input_length']} characters"]
    if not report["findings"]:
        lines.append("No suspicious characters found.")
        return "\n".join(lines)

    lines.append(
        f"{report['total_flagged']} flagged character(s), {report['total_removable']} removable"
    )
    lines.append("")
    header = f"{'CODEPOINT':<12} {'KIND':<19} {'CNT':>5} {'KEPT':>5}  NAME"
    lines.append(header)
    lines.append("-" * len(header))
    for f in report["findings"]:
        lines.append(
            f"{f['codepoint']:<12} {f['kind']:<19} {f['count']:>5} "
            f"{f['kept_as_load_bearing']:>5}  {f['name']}"
        )
        offsets = ", ".join(str(o) for o in f["offsets"])
        suffix = ", ..." if f["offsets_truncated"] else ""
        lines.append(f"{'':<12} at offsets: {offsets}{suffix}")
        if f["kept_reasons"]:
            reasons = ", ".join(f"{k}={v}" for k, v in f["kept_reasons"].items())
            lines.append(f"{'':<12} preserved as load-bearing: {reasons}")
        if f["confidence"] != "probable":
            lines.append(f"{'':<12} confidence: {f['confidence']}")
    lines.append("")
    lines.append("watermark suspected: " + ("yes" if report["watermark_suspected"] else "no"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = read_input(args.path)
        text = ensure_text(raw, force=args.force_text)
    except (InputTooLarge, BinaryInputRefused) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = scan(
        text,
        aggressive=args.aggressive,
        strip_emoji_glue=args.strip_emoji_glue,
    )
    report = result.to_dict()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render_human(report, args.path if args.path != "-" else "<stdin>"))

    return 1 if report["watermark_suspected"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
