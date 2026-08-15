"""JPEG APP-segment metadata stripping.

A JPEG is SOI followed by marker segments. Each segment is 0xFF, a marker byte,
then a 2-byte big-endian length that includes the length field itself. After
SOS the entropy-coded scan data runs until EOI, so we copy the tail verbatim.
"""

from __future__ import annotations

from .common import MetaResult

SOI = b"\xff\xd8"
EOI = b"\xff\xd9"

# Markers with no length field.
STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})

APP_KINDS: dict[int, str] = {
    0xE1: "exif",  # APP1: Exif or XMP
    0xE2: "icc",  # APP2: ICC_PROFILE, or MPF
    0xEB: "c2pa",  # APP11: JUMBF / C2PA
    0xED: "iptc",  # APP13: Photoshop IRB / IPTC
    0xEE: "adobe",  # APP14
}

# Segments that must survive: frame headers, quant/huffman tables, scan.
# fmt: off
KEEP_MARKERS = frozenset(
    {
        0xDB,  # DQT
        0xC4,  # DHT
        0xCC,  # DAC
        0xDD,  # DRI
        0xDA,  # SOS
        *range(0xC0, 0xC4),   # SOF0-2
        *range(0xC5, 0xC8),   # SOF3,5,6,7
        *range(0xC9, 0xCC),   # SOF9-11
        *range(0xCD, 0xD0),   # SOF13-15
    }
)
# fmt: on


def _segments(data: bytes):
    """Yield ``(marker, payload, raw, is_scan_tail)``."""
    if not data.startswith(SOI):
        raise ValueError("not a JPEG (missing SOI)")
    pos = 2
    n = len(data)
    while pos < n:
        if data[pos] != 0xFF:
            pos += 1
            continue
        # Skip fill bytes.
        m = pos + 1
        while m < n and data[m] == 0xFF:
            m += 1
        if m >= n:
            break
        marker = data[m]
        if marker == 0xD9:  # EOI
            yield marker, b"", data[pos : m + 1], False
            pos = m + 1
            if pos < n:
                yield None, b"", data[pos:], True
            return
        if marker in STANDALONE:
            yield marker, b"", data[pos : m + 1], False
            pos = m + 1
            continue
        if m + 3 > n:
            break
        length = int.from_bytes(data[m + 1 : m + 3], "big")
        end = m + 1 + length
        if length < 2 or end > n:
            raise ValueError("malformed JPEG segment length")
        payload = data[m + 3 : end]
        yield marker, payload, data[pos:end], False
        if marker == 0xDA:  # SOS: rest is entropy-coded data
            yield None, b"", data[end:], True
            return
        pos = end


def _app_kind(marker: int, payload: bytes) -> tuple[str, str] | None:
    """Return ``(kind, detail)`` for an APP segment worth stripping."""
    head = payload[:32]
    if marker == 0xE1:
        if head.startswith(b"Exif\x00"):
            return "exif", "APP1 Exif block"
        if head.startswith(b"http://ns.adobe.com/xap/"):
            return "xmp", "APP1 XMP packet"
        if head.startswith(b"http://ns.adobe.com/xmp/extension/"):
            return "xmp", "APP1 extended XMP packet"
        return "exif", "APP1 segment"
    if marker == 0xE2:
        if head.startswith(b"ICC_PROFILE\x00"):
            return "icc", "APP2 ICC profile"
        return "app2", "APP2 segment"
    if marker == 0xEB:
        return "c2pa", "APP11 JUMBF/C2PA manifest"
    if marker == 0xED:
        return "iptc", "APP13 Photoshop/IPTC block"
    if 0xE0 <= marker <= 0xEF:
        return f"app{marker - 0xE0}", f"APP{marker - 0xE0} segment"
    if marker == 0xFE:
        text = payload[:120].decode("latin-1", "replace")
        return "comment", f"COM segment: {text!r}"
    return None


def inspect(data: bytes) -> MetaResult:
    res = MetaResult(format="jpeg")
    try:
        for marker, payload, raw, tail in _segments(data):
            if tail or marker is None:
                continue
            info = _app_kind(marker, payload)
            if info:
                res.add(info[0], info[1], len(raw))
    except ValueError as exc:
        res.warnings.append(str(exc))
    return res


def clean(data: bytes) -> tuple[bytes, MetaResult]:
    res = inspect(data)
    out = bytearray()
    dropped = 0
    try:
        for marker, _payload, raw, tail in _segments(data):
            if tail or marker is None:
                out += raw
                continue
            if marker == 0xD8 or marker in KEEP_MARKERS or marker == 0xD9:
                out += raw
                continue
            if 0xE0 <= marker <= 0xEF or marker == 0xFE:
                # APP0/JFIF carries only density info; dropping it is safe and
                # avoids leaking writer identification.
                dropped += 1
                continue
            out += raw
    except ValueError as exc:
        res.warnings.append(f"clean skipped: {exc}")
        return data, res

    if not out.startswith(SOI):
        out[0:0] = SOI
    if dropped:
        res.actions.append(f"removed {dropped} JPEG APP/COM segment(s)")
    return bytes(out), res
