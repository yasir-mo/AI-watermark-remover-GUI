"""Fixture builders. Every fixture is generated, so the repo carries no binaries."""

from __future__ import annotations

import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --------------------------------------------------------------- text

#: Known invisibles injected into text, with the count of each.
INJECTED = {
    "​": 3,  # zero width space
    "⁠": 2,  # word joiner
    "﻿": 1,  # BOM
    "­": 1,  # soft hyphen
    "‮": 1,  # right-to-left override
    "\U000e0041": 4,  # tag character 'A'
    "️": 1,  # variation selector 16, standalone (not after emoji)
    "": 1,  # private use
    " ": 1,  # NBSP -> becomes the space between "brown" and "fox"
}

CLEAN_TEXT = "The quick brown fox jumps over the lazy dog. Pack my box."


@pytest.fixture
def clean_text() -> str:
    return CLEAN_TEXT


@pytest.fixture
def dirty_text() -> str:
    """CLEAN_TEXT with every character in INJECTED spliced in."""
    words = CLEAN_TEXT.split(" ")
    payload = "".join(ch * n for ch, n in INJECTED.items())
    return " ".join(words[:3]) + payload + " ".join(words[3:])


@pytest.fixture
def load_bearing_text() -> str:
    """Text whose invisibles are all doing real work."""
    return (
        "family \U0001f469‍\U0001f467 "  # ZWJ emoji sequence
        "persian پ‌ر "  # ZWNJ script joiner
        "heart ❤️ "  # VS16 emoji presentation
        "khmer ក឵"  # Khmer inherent vowel
    )


# --------------------------------------------------------------- images


def _png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


@pytest.fixture
def png_with_metadata() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"gAMA", struct.pack(">I", 45455))
        + _png_chunk(b"tEXt", b"Software\x00Midjourney v6")
        + _png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta/>")
        + _png_chunk(b"caBX", b"\x00\x00\x00\x20jumb c2pa manifest store")
        + _png_chunk(b"eXIf", b"II*\x00secret")
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
        + _png_chunk(b"IEND", b"")
    )


def _jpeg_seg(marker: int, payload: bytes) -> bytes:
    return b"\xff" + bytes([marker]) + struct.pack(">H", len(payload) + 2) + payload


SCAN_BYTES = b"\xfa\xfb\xfc\x01\x02"


@pytest.fixture
def jpeg_with_metadata() -> bytes:
    return (
        b"\xff\xd8"
        + _jpeg_seg(0xE0, b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00")
        + _jpeg_seg(0xE1, b"Exif\x00\x00MM\x00*\x00\x00\x00\x08camera-serial-1234")
        + _jpeg_seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>")
        + _jpeg_seg(0xEB, b"\x00\x00\x00\x18jumb\x00c2pa manifest")
        + _jpeg_seg(0xED, b"Photoshop 3.0\x008BIM")
        + _jpeg_seg(0xFE, b"Created with DALL-E 3")
        + _jpeg_seg(0xDB, b"\x00" + b"\x10" * 64)
        + _jpeg_seg(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
        + _jpeg_seg(0xC4, b"\x00\x00\x01" + b"\x00" * 15 + b"\x00")
        + _jpeg_seg(0xDA, b"\x01\x01\x00\x00")
        + SCAN_BYTES
        + b"\xff\xd9"
    )


def _riff_chunk(fourcc: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + pad


@pytest.fixture
def webp_with_metadata() -> bytes:
    body = (
        _riff_chunk(b"VP8X", bytes([0b0000_1100]) + b"\x00" * 9)
        + _riff_chunk(b"VP8 ", b"fake-image-data")
        + _riff_chunk(b"EXIF", b"II*\x00gps-coordinates")
        + _riff_chunk(b"XMP ", b"<x:xmpmeta/>")
        + _riff_chunk(b"C2PA", b"jumb manifest")
    )
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


# ------------------------------------------------------------ documents


@pytest.fixture
def docx_with_properties() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "docProps/core.xml",
            '<cp:coreProperties xmlns:cp="cp" xmlns:dc="dc">'
            "<dc:creator>Jane Author</dc:creator>"
            "<cp:lastModifiedBy>Claude</cp:lastModifiedBy>"
            "<dc:title>Quarterly Report</dc:title>"
            "</cp:coreProperties>",
        )
        z.writestr(
            "docProps/app.xml",
            "<Properties><Application>Microsoft Word</Application>"
            "<Company>ACME Corp</Company></Properties>",
        )
        z.writestr("customXml/item1.xml", "<c2pa>provenance</c2pa>")
        z.writestr("word/document.xml", "<w:document>Body text</w:document>")
    return buf.getvalue()


@pytest.fixture
def odt_with_properties() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        z.writestr(
            "meta.xml",
            '<office:document-meta xmlns:office="o" xmlns:meta="m" xmlns:dc="dc">'
            "<office:meta><meta:generator>LibreOffice/7.4</meta:generator>"
            "<meta:initial-creator>Jane</meta:initial-creator>"
            "<dc:creator>Jane</dc:creator></office:meta></office:document-meta>",
        )
        z.writestr("content.xml", "<office:document-content/>")
    return buf.getvalue()


@pytest.fixture
def pdf_with_metadata() -> bytes:
    return (
        b"%PDF-1.7\n"
        b"1 0 obj<</Title(Internal Draft)/Author(Jane Author)"
        b"/Creator(ChatGPT)/Producer(Acrobat)>>endobj\n"
        b"2 0 obj<</Type/Metadata/Subtype/XML>>stream\n"
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0"?><x:xmpmeta/>'
        b'<?xpacket end="w"?>\n'
        b"endstream endobj\n"
        b"trailer<</Info 1 0 R>>\n%%EOF"
    )


@pytest.fixture
def markdown_with_frontmatter() -> bytes:
    return (
        b"---\n"
        b"title: Real Title\n"
        b"ai_generated: true\n"
        b"model: gpt-4o\n"
        b"tags:\n"
        b"  - keep\n"
        b"---\n\n"
        b"Body paragraph with a zero\xe2\x80\x8bwidth space.\n"
    )


@pytest.fixture
def html_with_metadata() -> bytes:
    return (
        b"<!DOCTYPE html><html><head>"
        b'<meta charset="utf-8">'
        b'<meta name="generator" content="ChatGPT">'
        b'<script type="application/ld+json">{"@type":"AIGeneratedContent"}</script>'
        b'<script type="application/ld+json">{"@type":"Article"}</script>'
        b"</head>"
        b'<body data-ai-model="gpt-4o"><!-- generated by Copilot -->'
        b"<p>Hello</p></body></html>"
    )


@pytest.fixture
def svg_with_metadata() -> bytes:
    return (
        b'<?xml version="1.0"?>'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        b'data-ai-model="dalle3">'
        b"<!-- generated by Midjourney -->"
        b'<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
        b"</metadata>"
        b'<rect width="5" height="5"/></svg>'
    )
