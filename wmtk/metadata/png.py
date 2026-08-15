"""PNG chunk-level metadata stripping.

A PNG is a signature followed by chunks: 4-byte big-endian length, 4-byte type,
``length`` bytes of data, 4-byte CRC32 of (type + data). We keep only the
chunks that affect rendering and drop everything else.
"""

from __future__ import annotations

import struct
import zlib

from .common import MetaResult

SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Chunks required to render the image faithfully.
KEEP_CHUNKS: frozenset[bytes] = frozenset(
    {
        b"IHDR",
        b"PLTE",
        b"IDAT",
        b"IEND",
        b"gAMA",
        b"cHRM",
        b"sRGB",
        b"iCCP",
        b"sBIT",
        b"pHYs",
        b"tRNS",
        b"bKGD",
        b"acTL",
        b"fcTL",
        b"fdAT",  # APNG animation
    }
)

# Chunks that carry provenance / text / AI metadata.
STRIP_CHUNKS: dict[bytes, str] = {
    b"caBX": "c2pa",  # C2PA JUMBF box (PNG)
    b"c2pa": "c2pa",
    b"juMD": "c2pa",
    b"juMb": "c2pa",
    b"jUMb": "c2pa",
    b"iTXt": "xmp",  # XMP lives in an iTXt chunk keyed XML:com.adobe.xmp
    b"tEXt": "text",
    b"zTXt": "text",
    b"eXIf": "exif",
    b"xMPd": "xmp",
    b"tIME": "timestamp",
    b"sPLT": "text",
    b"hIST": "text",
}


def _chunks(data: bytes):
    """Yield ``(type, payload, whole_chunk_bytes)`` triples."""
    pos = len(SIGNATURE)
    n = len(data)
    while pos + 8 <= n:
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if length > n or end > n:
            raise ValueError("truncated or malformed PNG chunk")
        payload = data[pos + 8 : pos + 8 + length]
        yield ctype, payload, data[pos:end]
        pos = end
        if ctype == b"IEND":
            break


def _describe(ctype: bytes, payload: bytes) -> str:
    if ctype in (b"iTXt", b"tEXt", b"zTXt"):
        key = payload.split(b"\x00", 1)[0].decode("latin-1", "replace")
        return f"{ctype.decode('latin-1')} chunk, keyword {key!r}"
    return f"{ctype.decode('latin-1')} chunk"


def _kind(ctype: bytes, payload: bytes) -> str:
    kind = STRIP_CHUNKS.get(ctype, "unknown_chunk")
    if ctype == b"iTXt":
        key = payload.split(b"\x00", 1)[0]
        if key == b"XML:com.adobe.xmp":
            return "xmp"
        if b"c2pa" in key.lower():
            return "c2pa"
        return "text"
    return kind


def inspect(data: bytes) -> MetaResult:
    res = MetaResult(format="png")
    if not data.startswith(SIGNATURE):
        res.warnings.append("not a PNG signature")
        return res

    for ctype, payload, raw in _chunks(data):
        if ctype in KEEP_CHUNKS:
            continue
        res.add(_kind(ctype, payload), _describe(ctype, payload), len(raw))

        # A JUMBF-in-iTXt or a c2pa-labelled box is a manifest even if the
        # chunk type itself looked generic.
        if b"jumb" in payload[:64].lower() or b"c2pa" in payload[:256].lower():
            res.add("c2pa", f"C2PA/JUMBF signature inside {ctype.decode('latin-1')}", len(raw))
    return res


def clean(data: bytes) -> tuple[bytes, MetaResult]:
    res = inspect(data)
    if not data.startswith(SIGNATURE):
        return data, res

    out = bytearray(SIGNATURE)
    dropped = 0
    for ctype, _payload, raw in _chunks(data):
        if ctype in KEEP_CHUNKS:
            out += raw
        else:
            dropped += 1
    if dropped:
        res.actions.append(f"removed {dropped} non-rendering PNG chunk(s)")

    # Chunks are copied verbatim, so their CRCs remain valid; verify the ones
    # we kept rather than silently emitting a corrupt file.
    _verify_crcs(bytes(out))
    return bytes(out), res


def _verify_crcs(data: bytes) -> None:
    for ctype, payload, raw in _chunks(data):
        expected = struct.unpack(">I", raw[-4:])[0]
        actual = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        if expected != actual:
            raise ValueError(f"CRC mismatch on {ctype!r} chunk after rebuild")


def rebuild_chunk(ctype: bytes, payload: bytes) -> bytes:
    """Build a well-formed chunk with a fresh CRC (used when editing payloads)."""
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )
