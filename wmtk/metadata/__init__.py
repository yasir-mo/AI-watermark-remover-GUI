"""Format dispatch for file metadata inspection and cleaning.

``inspect_file`` and ``clean_file`` take raw bytes plus a filename hint and
route to the right handler. Handlers are stdlib-only; ``exiftool`` and
``c2patool`` are used to *enrich the report* and, for PDF, to do the actual
work, but are never required.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..safety import ToolFailed, have_tool, run_tool, safe_arg_path, temp_workdir
from . import html_md, jpeg, ooxml, pdf, png, svg, webp
from .common import MetaFinding, MetaResult

__all__ = [
    "SUPPORTED_FORMATS",
    "TEXT_FORMATS",
    "MetaFinding",
    "MetaResult",
    "clean_file",
    "detect_format",
    "inspect_file",
]

SUPPORTED_FORMATS = (
    "png",
    "jpeg",
    "webp",
    "svg",
    "pdf",
    "docx",
    "odt",
    "html",
    "markdown",
    "text",
)

# Formats whose bytes are also plain text, so Layer A applies to the body.
TEXT_FORMATS = frozenset({"svg", "html", "markdown", "text"})

_EXT_MAP: dict[str, str] = {
    ".png": "png",
    ".apng": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".jpe": "jpeg",
    ".jfif": "jpeg",
    ".webp": "webp",
    ".svg": "svg",
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "docx",
    ".pptx": "docx",
    ".odt": "odt",
    ".ods": "odt",
    ".odp": "odt",
    ".html": "html",
    ".htm": "html",
    ".xhtml": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".txt": "text",
    ".text": "text",
    ".json": "text",
    ".csv": "text",
}


def detect_format(name: str, data: bytes = b"") -> str:
    """Pick a handler from magic bytes first, extension second."""
    if data[:8] == png.SIGNATURE:
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        ext = Path(name).suffix.lower()
        return _EXT_MAP.get(ext, "docx")

    ext = Path(name).suffix.lower()
    if ext in _EXT_MAP:
        return _EXT_MAP[ext]

    head = data[:512].lstrip()
    if head[:5].lower() == b"<?xml" and b"<svg" in data[:2048].lower():
        return "svg"
    if head[:5].lower() == b"<svg ":
        return "svg"
    if head[:9].lower() == b"<!doctype" or head[:5].lower() == b"<html":
        return "html"
    return "text"


# --------------------------------------------------------------------------
# Optional-tool enrichment
# --------------------------------------------------------------------------


def _c2patool_report(path: Path) -> tuple[list[MetaFinding], list[str]]:
    """Ask c2patool whether the file carries a Content Credentials manifest."""
    findings: list[MetaFinding] = []
    warnings: list[str] = []
    try:
        proc = run_tool(["c2patool", safe_arg_path(path)], check=False)
    except Exception as exc:  # tool missing or blew up -- non-fatal
        return findings, [f"c2patool unavailable: {exc}"]

    stdout = proc.stdout.decode("utf-8", "replace")
    stderr = proc.stderr.decode("utf-8", "replace")

    if proc.returncode != 0:
        blob = (stderr + stdout).lower()
        if "no claim" in blob or "no c2pa" in blob or "jumbf" in blob:
            return findings, warnings  # genuinely no manifest -- not an error
        warnings.append(f"c2patool exited {proc.returncode}: {stderr.strip()[:200]}")
        return findings, warnings

    try:
        report = json.loads(stdout)
    except ValueError:
        if stdout.strip():
            findings.append(MetaFinding("c2pa", "c2patool reported a manifest", 0))
        return findings, warnings

    active = report.get("active_manifest")
    manifests = report.get("manifests", {})
    if active or manifests:
        findings.append(
            MetaFinding(
                "c2pa",
                f"C2PA manifest store: {len(manifests)} manifest(s), active={active}",
                0,
                "confirmed",
            )
        )
        for label, manifest in manifests.items():
            gen = manifest.get("claim_generator", "?")
            findings.append(
                MetaFinding("c2pa", f"manifest {label}: claim_generator={gen}", 0, "confirmed")
            )
    if report.get("validation_status"):
        findings.append(
            MetaFinding("c2pa", f"validation_status: {report['validation_status']}", 0)
        )
    return findings, warnings


def _exiftool_report(path: Path) -> tuple[list[MetaFinding], list[str]]:
    """List every tag exiftool can see, grouped."""
    findings: list[MetaFinding] = []
    warnings: list[str] = []
    try:
        proc = run_tool(["exiftool", "-j", "-G", "-n", "-a", safe_arg_path(path)], check=False)
    except Exception as exc:
        return findings, [f"exiftool unavailable: {exc}"]

    try:
        entries = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return findings, ["exiftool produced unparseable JSON"]

    for entry in entries:
        for tag, value in entry.items():
            if ":" not in tag:
                continue
            group, name = tag.split(":", 1)
            kind = _exiftool_kind(group, name)
            if kind is None:
                continue
            findings.append(MetaFinding(kind, f"{tag} = {str(value)[:120]}", 0, "confirmed"))
    return findings, warnings


#: Groups that carry provenance, authorship or history. Anything outside this
#: set is structural and is not metadata in the sense this tool cares about.
_EXIFTOOL_GROUPS: dict[str, str] = {
    "EXIF": "exif",
    "XMP": "xmp",
    "IPTC": "iptc",
    "ICC_Profile": "icc",
    "JUMBF": "c2pa",
    "C2PA": "c2pa",
    "Photoshop": "iptc",
    "PDF": "docprops",
    "MakerNotes": "exif",
    "GPS": "exif",
    "Adobe": "adobe",
    "FlashPix": "docprops",
    "Ducky": "adobe",
    "MIE": "docprops",
}

#: Textual tags worth reporting from otherwise-structural format groups.
#: `PNG:ImageWidth` is a property of the picture; `PNG:Software` is a claim
#: about who made it.
_EXIFTOOL_TEXT_TAGS: frozenset[str] = frozenset(
    {
        "Software",
        "Comment",
        "Description",
        "Author",
        "Artist",
        "Copyright",
        "Title",
        "Warning",
        "Disclaimer",
        "Source",
        "CreationTime",
        "ModifyDate",
        "CreateDate",
        "Creator",
        "Producer",
        "Keywords",
        "UserComment",
        "DocumentName",
        "HostComputer",
        "ProcessingSoftware",
        "XMLcomAdobeXMP",
        "Parameters",
        "Prompt",
        "Workflow",
    }
)


def _exiftool_kind(group: str, name: str) -> str | None:
    """Map an exiftool group/tag to a finding kind, or None to ignore it.

    An allowlist, not a denylist. exiftool reports every structural property it
    can read -- image dimensions, bit depth, colour type -- and treating those
    as findings meant a freshly cleaned PNG still reported a pile of
    "metadata", which makes the clean-then-verify loop useless. Format-native
    text chunks are still caught, both by the tag list below and by our own
    parsers, which read them directly.
    """
    if group in _EXIFTOOL_GROUPS:
        return _EXIFTOOL_GROUPS[group]
    if name in _EXIFTOOL_TEXT_TAGS:
        return "text"
    return None


def _enrich(result: MetaResult, data: bytes, name: str) -> None:
    """Add exiftool/c2patool observations to a stdlib result."""
    if not (have_tool("exiftool") or have_tool("c2patool")):
        result.warnings.append(
            "exiftool and c2patool are not installed; report is from the built-in parsers only"
        )
        return

    with temp_workdir() as tmp:
        path = Path(tmp) / (Path(name).name or "input.bin")
        path.write_bytes(data)

        if have_tool("exiftool"):
            found, warns = _exiftool_report(path)
            result.findings.extend(found)
            result.warnings.extend(warns)
        if have_tool("c2patool"):
            found, warns = _c2patool_report(path)
            result.findings.extend(found)
            result.warnings.extend(warns)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_INSPECTORS = {
    "png": png.inspect,
    "jpeg": jpeg.inspect,
    "webp": webp.inspect,
    "svg": svg.inspect,
    "pdf": pdf.inspect,
    "docx": ooxml.inspect,
    "odt": ooxml.inspect,
    "html": html_md.inspect_html,
    "markdown": html_md.inspect_markdown,
}

_CLEANERS = {
    "png": png.clean,
    "jpeg": jpeg.clean,
    "webp": webp.clean,
    "svg": svg.clean,
    "pdf": pdf.clean,
    "docx": ooxml.clean,
    "odt": ooxml.clean,
    "html": html_md.clean_html,
    "markdown": html_md.clean_markdown,
}


def inspect_file(data: bytes, name: str = "input.bin", *, use_tools: bool = True) -> MetaResult:
    fmt = detect_format(name, data)
    inspector = _INSPECTORS.get(fmt)
    if inspector is None:
        result = MetaResult(format=fmt)
    else:
        result = inspector(data)
        result.format = fmt
    if use_tools and fmt not in ("text", "markdown"):
        _enrich(result, data, name)
    return result


def clean_file(data: bytes, name: str = "input.bin") -> tuple[bytes, MetaResult]:
    fmt = detect_format(name, data)
    cleaner = _CLEANERS.get(fmt)
    if cleaner is None:
        return data, MetaResult(format=fmt)

    # For images, run exiftool as a belt-and-braces second pass when present;
    # our parsers are conservative and exiftool knows about vendor oddities.
    out, result = cleaner(data)
    result.format = fmt

    if fmt in ("png", "jpeg", "webp") and have_tool("exiftool"):
        out = _exiftool_scrub(out, name, result)
    return out, result


def _exiftool_scrub(data: bytes, name: str, result: MetaResult) -> bytes:
    with temp_workdir() as tmp:
        path = Path(tmp) / (Path(name).name or "input.bin")
        path.write_bytes(data)
        try:
            run_tool(
                [
                    "exiftool",
                    "-all=",
                    "-overwrite_original",
                    "-q",
                    "-q",
                    safe_arg_path(path),
                ]
            )
        except ToolFailed as exc:
            result.warnings.append(f"exiftool second pass failed: {exc}")
            return data
        result.actions.append("exiftool -all= second pass applied")
        result.tier = "stdlib+exiftool"
        return path.read_bytes()
