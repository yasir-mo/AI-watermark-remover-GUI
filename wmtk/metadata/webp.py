"""WebP (RIFF container) metadata stripping.

RIFF layout: ``RIFF`` + 4-byte LE size + ``WEBP`` + a sequence of chunks, each
being a 4-byte FourCC + 4-byte LE size + payload + optional pad byte to even
length. Metadata lives in EXIF, XMP, and (for C2PA) the ``C2PA`` chunk.
"""

from __future__ import annotations

import struct

from .common import MetaResult

KEEP_FOURCC: frozenset[bytes] = frozenset(
    {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF", b"ICCP"}
)

STRIP_KINDS: dict[bytes, str] = {
    b"EXIF": "exif",
    b"XMP ": "xmp",
    b"C2PA": "c2pa",
    b"JUMB": "c2pa",
}


def _chunks(data: bytes):
    pos = 12
    n = len(data)
    while pos + 8 <= n:
        fourcc = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        padded = size + (size & 1)
        end = pos + 8 + padded
        if end > n:
            raise ValueError("truncated RIFF chunk")
        yield fourcc, data[pos + 8 : pos + 8 + size], data[pos:end]
        pos = end


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def inspect(data: bytes) -> MetaResult:
    res = MetaResult(format="webp")
    if not _is_webp(data):
        res.warnings.append("not a RIFF/WEBP container")
        return res
    try:
        for fourcc, _payload, raw in _chunks(data):
            if fourcc in KEEP_FOURCC:
                continue
            kind = STRIP_KINDS.get(fourcc, "unknown_chunk")
            res.add(kind, f"{fourcc.decode('latin-1')} RIFF chunk", len(raw))
    except ValueError as exc:
        res.warnings.append(str(exc))
    return res


def clean(data: bytes) -> tuple[bytes, MetaResult]:
    res = inspect(data)
    if not _is_webp(data):
        return data, res

    body = bytearray()
    dropped = 0
    has_meta_flag = False
    try:
        for fourcc, payload, raw in _chunks(data):
            if fourcc in KEEP_FOURCC:
                if fourcc == b"VP8X":
                    # Clear the EXIF (bit 3) and XMP (bit 2) flags in the
                    # extended-format header so readers stop looking for them.
                    flags = payload[0] if payload else 0
                    if flags & 0b0000_1100:
                        has_meta_flag = True
                    new = bytes([flags & ~0b0000_1100]) + payload[1:]
                    body += _pack(fourcc, new)
                    continue
                body += raw
            else:
                dropped += 1
    except ValueError as exc:
        res.warnings.append(f"clean skipped: {exc}")
        return data, res

    out = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + bytes(body)
    if dropped:
        res.actions.append(f"removed {dropped} RIFF metadata chunk(s)")
    if has_meta_flag:
        res.actions.append("cleared EXIF/XMP flags in VP8X header")
    return out, res


def _pack(fourcc: bytes, payload: bytes) -> bytes:
    pad = b"\x00" if len(payload) & 1 else b""
    return fourcc + struct.pack("<I", len(payload)) + payload + pad
