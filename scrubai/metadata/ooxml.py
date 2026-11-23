"""DOCX / XLSX / PPTX / ODT metadata stripping.

All of these are ZIP containers. We unzip in memory, scrub the property parts,
drop AI-provenance parts entirely, and rezip. Entry order and compression type
are preserved so the output stays a valid package.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from .common import MalformedFile, MetaResult
from .safexml import UnsafeXml, fromstring

MAX_MEMBERS = 4096
MAX_UNCOMPRESSED = 512 * 1024 * 1024

# OOXML property parts.
OOXML_CORE = "docProps/core.xml"
OOXML_APP = "docProps/app.xml"

# Fields inside core.xml that identify the author / tool / timeline.
# fmt: off
CORE_STRIP_LOCALS = {
    "creator", "lastModifiedBy", "lastPrinted", "revision",
    "created", "modified", "category", "contentStatus", "description",
    "keywords", "identifier", "language", "version",
}
APP_STRIP_LOCALS = {
    "Application", "AppVersion", "Company", "Manager", "Template",
    "TotalTime", "DocSecurity", "HyperlinkBase",
}

# ODT: meta.xml holds the equivalent fields.
ODT_META = "meta.xml"
ODT_STRIP_LOCALS = {
    "generator", "creation-date", "date", "editing-cycles",
    "editing-duration", "initial-creator", "creator", "printed-by",
    "print-date", "keyword", "subject", "description", "title",
    "user-defined",
}
# fmt: on

AI_PART_RE = re.compile(
    r"(customXml/|docProps/custom\.xml$|.*c2pa.*|.*contentcredentials.*)", re.I
)
AI_CONTENT_RE = re.compile(
    rb"(c2pa|contentcredentials|AIGeneratedContent|ai[-_]generated"
    rb"|openai|anthropic|claude|gpt-4|copilot)",
    re.I,
)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _detect_format(names: list[str]) -> str:
    if ODT_META in names or "mimetype" in names:
        return "odt"
    return "docx"


def _open(data: bytes) -> zipfile.ZipFile:
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ValueError(f"zip has {len(infos)} members, limit {MAX_MEMBERS}")
    total = sum(i.file_size for i in infos)
    if total > MAX_UNCOMPRESSED:
        raise ValueError(f"zip expands to {total} bytes, limit {MAX_UNCOMPRESSED}")
    for i in infos:
        if i.filename.startswith("/") or ".." in i.filename.split("/"):
            raise ValueError(f"unsafe zip member path: {i.filename!r}")
    return zf


def _strip_props_xml(raw: bytes, locals_to_strip: set[str]) -> tuple[bytes, list[str]]:
    """Remove matching child elements from a properties part."""
    try:
        root = fromstring(raw)
    except (ET.ParseError, UnsafeXml):
        return raw, []
    removed: list[str] = []
    for parent in list(root.iter()):
        for child in list(parent):
            if _localname(child.tag) in locals_to_strip:
                parent.remove(child)
                removed.append(_localname(child.tag))
    if not removed:
        return raw, []
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), removed


def inspect(data: bytes) -> MetaResult:
    try:
        zf = _open(data)
    except (zipfile.BadZipFile, ValueError) as exc:
        res = MetaResult(format="ooxml")
        res.warnings.append(str(exc))
        return res

    names = zf.namelist()
    res = MetaResult(format=_detect_format(names))

    for name in names:
        if AI_PART_RE.match(name):
            res.add("ai_part", f"package part {name}", zf.getinfo(name).file_size)

    def report_props(part: str, locals_to_strip: set[str]) -> None:
        if part not in names:
            return
        raw = zf.read(part)
        try:
            root = fromstring(raw)
        except (ET.ParseError, UnsafeXml) as exc:
            res.warnings.append(f"{part} is not parseable XML: {exc}")
            return
        for el in root.iter():
            local = _localname(el.tag)
            if local in locals_to_strip:
                value = (el.text or "").strip()[:120]
                res.add("docprops", f"{part}: {local} = {value!r}")

    report_props(OOXML_CORE, CORE_STRIP_LOCALS)
    report_props(OOXML_APP, APP_STRIP_LOCALS)
    report_props(ODT_META, ODT_STRIP_LOCALS)

    for name in names:
        if name.endswith(".xml") or name.endswith(".rels"):
            body = zf.read(name)
            m = AI_CONTENT_RE.search(body)
            if m:
                res.add(
                    "ai_marker",
                    f"{name} contains {m.group(0).decode('latin-1')!r}",
                )
    return res


def clean(data: bytes) -> tuple[bytes, MetaResult]:
    res = inspect(data)
    try:
        zf = _open(data)
    except (zipfile.BadZipFile, ValueError) as exc:
        # Returning `data` here would hand back an untouched file as "cleaned".
        raise MalformedFile(f"cannot open package: {exc}") from exc

    buf = io.BytesIO()
    dropped: list[str] = []
    scrubbed: list[str] = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as out:
        for info in zf.infolist():
            name = info.filename
            if AI_PART_RE.match(name):
                dropped.append(name)
                continue
            raw = zf.read(name)

            if name == OOXML_CORE:
                raw, removed = _strip_props_xml(raw, CORE_STRIP_LOCALS)
                if removed:
                    scrubbed.append(f"{name} ({len(removed)} fields)")
            elif name == OOXML_APP:
                raw, removed = _strip_props_xml(raw, APP_STRIP_LOCALS)
                if removed:
                    scrubbed.append(f"{name} ({len(removed)} fields)")
            elif name == ODT_META:
                raw, removed = _strip_props_xml(raw, ODT_STRIP_LOCALS)
                if removed:
                    scrubbed.append(f"{name} ({len(removed)} fields)")

            # The ODF spec requires `mimetype` to be the first entry and stored
            # uncompressed.
            compress = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            new_info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            new_info.compress_type = compress
            new_info.external_attr = info.external_attr
            out.writestr(new_info, raw)

    if dropped:
        res.actions.append(f"dropped part(s): {', '.join(dropped)}")
    if scrubbed:
        res.actions.append(f"scrubbed properties in {', '.join(scrubbed)}")
    res.actions.append("normalized all zip timestamps to 1980-01-01")
    return buf.getvalue(), res
