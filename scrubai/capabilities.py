"""Runtime capability discovery.

Reports which optional external tools and heavy backends are reachable, so the
UI can grey out what is unavailable and tell the user how to install it.
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from . import __version__
from .safety import MAX_INPUT_BYTES, MAX_TEXT_BYTES, run_tool

TOOLS: dict[str, dict[str, str]] = {
    "exiftool": {
        "enables": "Full EXIF/XMP/IPTC reporting and a second scrub pass on images and PDFs.",
        "install": "apt install libimage-exiftool-perl  |  brew install exiftool  |  winget install ExifTool",
    },
    "c2patool": {
        "enables": "Reads and verifies C2PA / Content Credentials manifests.",
        "install": "cargo install c2patool  |  download from github.com/contentauth/c2patool/releases",
    },
    "qpdf": {
        "enables": "Structural PDF rebuild, so the metadata bytes leave the file.",
        "install": "apt install qpdf  |  brew install qpdf  |  winget install qpdf.qpdf",
    },
}

BACKEND_ENV: dict[str, dict[str, str]] = {
    "harnesses.markllm": {
        "env": "WATERMARKS_MARKLLM_PATH",
        "enables": "KGW / SynthID statistical watermark detection via THU-BPM MarkLLM.",
        "install": "bash scripts/setup_markllm.sh  (clones MarkLLM into ./vendor and builds a venv)",
    },
    "scorers.synthid": {
        "env": "WATERMARKS_SYNTHID_PATH",
        "enables": "SynthID text scoring in a separate container.",
        "install": "docker compose build synthid",
    },
    "pixel_backends.ctrlregen": {
        "env": "WATERMARKS_CTRLREGEN_PATH",
        "enables": "Regeneration-based removal of pixel-domain image watermarks.",
        "install": "docker compose build ctrlregen",
    },
}


def _tool_version(name: str) -> str | None:
    flag = {"exiftool": "-ver", "c2patool": "--version", "qpdf": "--version"}[name]
    try:
        proc = run_tool([name, flag], timeout=15, check=False)
    except Exception:
        return None
    out = (proc.stdout or proc.stderr).decode("utf-8", "replace").strip()
    return out.splitlines()[0][:80] if out else None


def capabilities() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name, meta in TOOLS.items():
        path = shutil.which(name)
        tools[name] = {
            "installed": path is not None,
            "path": path,
            "version": _tool_version(name) if path else None,
            "enables": meta["enables"],
            "install": meta["install"],
        }

    backends: dict[str, Any] = {}
    for key, meta in BACKEND_ENV.items():
        configured = os.environ.get(meta["env"])
        backends[key] = {
            "configured": bool(configured),
            "path": configured,
            "env_var": meta["env"],
            "enables": meta["enables"],
            "install": meta["install"],
        }

    from .metadata import SUPPORTED_FORMATS
    from .rewrite import BACKENDS, STRENGTHS

    return {
        "version": __version__,
        "tools": tools,
        "backends": backends,
        "layer_a": {"available": True, "requires": "stdlib only"},
        "layer_b": {
            "available": True,
            "backends": list(BACKENDS),
            "strengths": list(STRENGTHS),
            "api_key_env": "WATERMARKS_REWRITE_API_KEY",
        },
        "formats": list(SUPPORTED_FORMATS),
        "limits": {
            "max_input_bytes": MAX_INPUT_BYTES,
            "max_text_bytes": MAX_TEXT_BYTES,
        },
        "pdf_tier": (
            "exiftool+qpdf"
            if tools["exiftool"]["installed"] and tools["qpdf"]["installed"]
            else "exiftool"
            if tools["exiftool"]["installed"]
            else "stdlib"
        ),
    }
