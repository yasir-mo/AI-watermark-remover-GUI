"""CLI: statistical watermark detection via THU-BPM/MarkLLM (optional backend).

MarkLLM (Apache-2.0) is never vendored. It is imported at runtime from a
checkout the user provides, either via ``--markllm-path`` or the
``WATERMARKS_MARKLLM_PATH`` environment variable. Run ``scripts/setup_markllm.sh``
to create one.

Scope note: detection is only meaningful against the *same scheme with the same
key* used at generation time. A "not watermarked" verdict here says the text
does not carry a KGW/SynthID mark under the config you supplied. It says
nothing about other schemes, other keys, or other vendors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCHEME_MAP = {
    "kgw": "KGW",
    "synthid": "SynthID",
    "synthid-text": "SynthID",
}

DEFAULT_MODEL = "facebook/opt-1.3b"


class HarnessError(RuntimeError):
    pass


def _resolve_checkout(explicit: str | None) -> Path:
    raw = explicit or os.environ.get("WATERMARKS_MARKLLM_PATH")
    if not raw:
        raise HarnessError(
            "MarkLLM checkout not configured. Run scripts/setup_markllm.sh, then "
            "set WATERMARKS_MARKLLM_PATH or pass --markllm-path."
        )
    path = Path(raw).expanduser().resolve()
    if not (path / "watermark").is_dir():
        raise HarnessError(f"{path} does not look like a MarkLLM checkout")
    return path


def _load_autowatermark(checkout: Path):
    if str(checkout) not in sys.path:
        sys.path.insert(0, str(checkout))
    try:
        from watermark.auto_watermark import AutoWatermark  # type: ignore
    except ImportError as exc:
        raise HarnessError(
            f"cannot import MarkLLM from {checkout}: {exc}. "
            f"Activate the venv created by scripts/setup_markllm.sh."
        ) from exc
    return AutoWatermark


def _build_transformers_config(model_name: str, offline: bool):
    try:
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
    except ImportError as exc:
        raise HarnessError(
            f"torch/transformers not available: {exc}. "
            f"Activate the MarkLLM venv (vendor/markllm-venv)."
        ) from exc

    from utils.transformers_config import TransformersConfig  # type: ignore

    kwargs = {"local_files_only": True} if offline else {}
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = AutoModelForCausalLM.from_pretrained(model_name, **kwargs).to(device)
    tokenizer = AutoTokenizer.from_pretrained(model_name, **kwargs)
    return TransformersConfig(model=model, tokenizer=tokenizer, device=device)


def _config_path(checkout: Path, scheme: str) -> Path:
    path = checkout / "config" / f"{SCHEME_MAP[scheme]}.json"
    if not path.is_file():
        raise HarnessError(f"scheme config not found: {path}")
    return path


def _threshold_from_config(path: Path) -> tuple[float | None, str | None]:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    for key in ("threshold", "z_threshold", "detection_threshold"):
        if key in cfg:
            return float(cfg[key]), key
    return None, None


def cmd_detect(args: argparse.Namespace) -> int:
    checkout = _resolve_checkout(args.markllm_path)
    AutoWatermark = _load_autowatermark(checkout)

    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    cfg_path = _config_path(checkout, args.scheme)
    transformers_config = _build_transformers_config(args.model, args.offline)

    wm = AutoWatermark.load(
        SCHEME_MAP[args.scheme],
        algorithm_config=str(cfg_path),
        transformers_config=transformers_config,
    )

    text = _read_text(args.path)
    result = wm.detect_watermark(text, return_dict=True)
    threshold, threshold_key = _threshold_from_config(cfg_path)

    payload = {
        "scheme": SCHEME_MAP[args.scheme],
        "model": args.model,
        "config": str(cfg_path),
        "is_watermarked": bool(result.get("is_watermarked")),
        "score": result.get("score"),
        "threshold": threshold,
        "threshold_key": threshold_key,
        "raw": {k: _jsonable(v) for k, v in result.items()},
        "scope": (
            "Valid only against this scheme with this key and config. A negative "
            "result does not rule out other schemes, keys, or vendors."
        ),
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["is_watermarked"] else 1


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate watermarked and unwatermarked samples for testing detection."""
    checkout = _resolve_checkout(args.markllm_path)
    AutoWatermark = _load_autowatermark(checkout)

    if args.seed is not None:
        import torch  # type: ignore

        torch.manual_seed(args.seed)

    cfg_path = _config_path(checkout, args.scheme)
    transformers_config = _build_transformers_config(args.model, args.offline)
    transformers_config.gen_kwargs = {
        "max_new_tokens": args.max_new_tokens,
        "min_length": args.min_length,
        "do_sample": True,
    }

    wm = AutoWatermark.load(
        SCHEME_MAP[args.scheme],
        algorithm_config=str(cfg_path),
        transformers_config=transformers_config,
    )

    watermarked = wm.generate_watermarked_text(args.prompt)
    payload = {
        "scheme": SCHEME_MAP[args.scheme],
        "prompt": args.prompt,
        "watermarked": watermarked,
    }
    if args.with_unwatermarked:
        payload["unwatermarked"] = wm.generate_unwatermarked_text(args.prompt)

    print(json.dumps(payload, indent=2))
    return 0


def _read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8", errors="replace")


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="detect-text-watermark",
        description="Statistical watermark detection via an external MarkLLM checkout.",
    )
    p.add_argument("--markllm-path", help="path to a MarkLLM checkout")
    p.add_argument("--model", default=DEFAULT_MODEL, help="HuggingFace model id")
    p.add_argument("--scheme", choices=sorted(SCHEME_MAP), default="kgw")
    p.add_argument("--offline", action="store_true", help="HF_HUB_OFFLINE=1, local files only")

    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("detect", help="score a text against a scheme")
    d.add_argument("path", nargs="?", default="-")
    d.set_defaults(func=cmd_detect)

    g = sub.add_parser("generate", help="produce watermarked sample text for testing")
    g.add_argument("prompt")
    g.add_argument("--max-new-tokens", type=int, default=200)
    g.add_argument("--min-length", type=int, default=230)
    g.add_argument("--seed", type=int)
    g.add_argument("--with-unwatermarked", action="store_true")
    g.set_defaults(func=cmd_generate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
