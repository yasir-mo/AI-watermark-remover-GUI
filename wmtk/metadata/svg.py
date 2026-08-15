"""SVG metadata stripping.

SVG is XML, so this uses ``xml.etree`` with entity expansion left at the stdlib
default and external DTD loading unavailable -- ElementTree does not resolve
external entities, which is what we want for untrusted input.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .common import MetaResult
from .safexml import UnsafeXml, fromstring

SVG_NS = "http://www.w3.org/2000/svg"

# Namespace prefixes whose attributes and elements are provenance metadata.
META_NS_MARKERS = ("adobe.com/xap", "purl.org/dc/", "w3.org/1999/02/22-rdf")

META_TAGS = {"metadata", "desc", "title"}

AI_ATTR_RE = re.compile(r"^(data-ai-|ai-|data-c2pa)", re.I)
AI_COMMENT_RE = re.compile(
    rb"<!--(?:(?!-->).)*?(generated|midjourney|dall[\s\-]?e|stable[\s\-]?diffusion"
    rb"|openai|anthropic|c2pa|firefly|imagen).*?-->",
    re.I | re.S,
)
XMP_PACKET_RE = re.compile(rb"<\?xpacket.*?<\?xpacket[^>]*\?>", re.S)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _parse(data: bytes) -> ET.Element:
    return fromstring(data)


def inspect(data: bytes) -> MetaResult:
    res = MetaResult(format="svg")

    for m in XMP_PACKET_RE.finditer(data):
        res.add("xmp", "XMP xpacket block", len(m.group(0)))
    for m in AI_COMMENT_RE.finditer(data):
        text = m.group(0)[:120].decode("utf-8", "replace")
        res.add("comment", f"AI-related comment: {text!r}", len(m.group(0)))

    try:
        root = _parse(data)
    except (ET.ParseError, UnsafeXml) as exc:
        res.warnings.append(f"XML parse failed: {exc}")
        return res

    for el in root.iter():
        name = _localname(el.tag)
        if name in META_TAGS:
            res.add(
                "xmp" if name == "metadata" else "docprops",
                f"<{name}> element",
                len(ET.tostring(el)),
            )
        for attr in el.attrib:
            local = _localname(attr)
            if AI_ATTR_RE.match(local) or any(m in attr for m in META_NS_MARKERS):
                res.add("attribute", f"<{name} {local}=...>")
    return res


def clean(data: bytes) -> tuple[bytes, MetaResult]:
    res = inspect(data)

    stripped = XMP_PACKET_RE.sub(b"", data)
    stripped = AI_COMMENT_RE.sub(b"", stripped)

    try:
        root = _parse(stripped)
    except (ET.ParseError, UnsafeXml) as exc:
        res.warnings.append(f"XML parse failed, only regex pass applied: {exc}")
        return stripped, res

    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            if _localname(child.tag) in META_TAGS:
                parent.remove(child)
                removed += 1

    attrs_removed = 0
    for el in root.iter():
        for attr in list(el.attrib):
            local = _localname(attr)
            if AI_ATTR_RE.match(local) or any(m in attr for m in META_NS_MARKERS):
                del el.attrib[attr]
                attrs_removed += 1

    ET.register_namespace("", SVG_NS)
    out = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    if removed:
        res.actions.append(f"removed {removed} metadata element(s)")
    if attrs_removed:
        res.actions.append(f"removed {attrs_removed} metadata attribute(s)")
    res.actions.append("re-serialized XML (whitespace and prefixes may differ)")
    return out, res
