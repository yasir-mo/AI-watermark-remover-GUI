"""PDF metadata stripping, in three tiers.

Tier 1 (best)     exiftool -all= followed by ``qpdf --linearize``. qpdf rewrites
                  the whole file structure, so the old metadata bytes are gone
                  and are no longer present in the file.
Tier 2 (middle)   exiftool only. This is an incremental update: the original
                  metadata objects usually remain in the file, orphaned but
                  recoverable with a hex editor.
Tier 3 (degraded) stdlib regex removal of XMP packets and /Info string values.
                  Object streams and compressed metadata are not reachable this
                  way, so this tier is best-effort only.

The tier that actually ran is always reported.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..safety import have_tool, run_tool, safe_arg_path, temp_workdir
from .common import MetaResult

XMP_PACKET_RE = re.compile(rb"<\?xpacket begin=.*?<\?xpacket end=[^>]*\?>", re.S)
METADATA_OBJ_RE = re.compile(rb"/Type\s*/Metadata", re.S)

INFO_KEYS = (
    b"Title",
    b"Author",
    b"Subject",
    b"Keywords",
    b"Creator",
    b"Producer",
    b"CreationDate",
    b"ModDate",
    b"Trapped",
)

AI_MARKERS = (
    b"c2pa",
    b"contentcredentials",
    b"AIGeneratedContent",
    b"Midjourney",
    b"DALL",
    b"Stable Diffusion",
    b"Firefly",
)


def inspect(data: bytes) -> MetaResult:
    res = MetaResult(format="pdf", tier=_available_tier())

    for m in XMP_PACKET_RE.finditer(data):
        res.add("xmp", "XMP metadata packet", len(m.group(0)))
    if METADATA_OBJ_RE.search(data):
        res.add("xmp", "/Type /Metadata object present")

    for key in INFO_KEYS:
        pat = re.compile(rb"/" + key + rb"\s*\((?:[^()\\]|\\.)*\)", re.S)
        for m in pat.finditer(data):
            value = m.group(0)[len(key) + 1 :][:120].decode("latin-1", "replace")
            res.add("docprops", f"/{key.decode()} = {value}", len(m.group(0)))

    lowered = data.lower()
    for marker in AI_MARKERS:
        if marker.lower() in lowered:
            res.add(
                "c2pa" if b"c2pa" in marker.lower() else "ai_marker",
                f"byte marker {marker.decode('latin-1')!r} present",
            )
    return res


def _available_tier() -> str:
    if have_tool("exiftool") and have_tool("qpdf"):
        return "exiftool+qpdf"
    if have_tool("exiftool"):
        return "exiftool"
    return "stdlib"


def clean(data: bytes) -> tuple[bytes, MetaResult]:
    res = inspect(data)
    tier = res.tier

    if tier == "stdlib":
        res.warnings.append(
            "degraded tier: regex-only removal. Metadata inside object streams "
            "or compressed cross-reference streams is not reachable. "
            "Install exiftool and qpdf for a structural rebuild."
        )
        return _clean_stdlib(data, res), res

    with temp_workdir() as tmp:
        src = Path(tmp) / "in.pdf"
        src.write_bytes(data)

        run_tool(
            [
                "exiftool",
                "-all=",
                "-XMP:all=",
                "-overwrite_original",
                "-q",
                "-q",
                safe_arg_path(src),
            ]
        )
        out_bytes = src.read_bytes()
        res.actions.append("exiftool -all= applied")

        if tier == "exiftool+qpdf":
            dst = Path(tmp) / "out.pdf"
            run_tool(
                [
                    "qpdf",
                    "--linearize",
                    "--object-streams=generate",
                    safe_arg_path(src),
                    safe_arg_path(dst),
                ]
            )
            out_bytes = dst.read_bytes()
            res.actions.append("qpdf structural rebuild (--linearize) applied")
        else:
            res.warnings.append(
                "middle tier: exiftool performs an incremental update, so the "
                "original metadata bytes may remain in the file unreferenced. "
                "Install qpdf for a full rebuild."
            )

    return out_bytes, res


def _clean_stdlib(data: bytes, res: MetaResult) -> bytes:
    """Overwrite metadata in place with spaces, keeping every byte offset.

    Deleting bytes would shift every later object, leaving the xref table and
    the metadata stream's /Length wrong. Overwriting with whitespace at the same
    length leaves the file structurally identical: the XMP stream becomes
    blank, and each /Info string becomes ``()`` followed by padding, which PDF
    treats as insignificant whitespace between dictionary entries.
    """

    def blank(m: re.Match[bytes]) -> bytes:
        return b" " * len(m.group(0))

    out, n_xmp = XMP_PACKET_RE.subn(blank, data)
    if n_xmp:
        res.actions.append(f"blanked {n_xmp} XMP packet(s) in place")

    def blank_value(m: re.Match[bytes]) -> bytes:
        key, value = m.group(1), m.group(2)
        return key + b"()" + b" " * (len(value) - 2)

    total = 0
    for key in INFO_KEYS:
        pat = re.compile(rb"(/" + key + rb"\s*)(\((?:[^()\\]|\\.)*\))", re.S)
        out, n = pat.subn(blank_value, out)
        total += n
    if total:
        res.actions.append(f"blanked {total} /Info string value(s) in place")
    return out
