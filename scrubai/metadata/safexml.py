"""XML parsing hardened against entity attacks.

``xml.etree.ElementTree`` is documented as vulnerable to entity-expansion
("billion laughs") attacks, and the SVG and OOXML input here is untrusted.
Pulling in ``defusedxml`` would break the stdlib-only promise, so instead we
reject the constructs that make those attacks possible *before* handing bytes
to the parser.

The check happens at the byte level, before any parsing. ``ET.XMLParser``
stopped exposing its underlying expat object in Python 3.14, so a
handler-based guard has no effect there and permits everything it was meant to
block. This check does not depend on interpreter internals.

Well-formed SVG and Office documents never declare entities, so nothing
legitimate is rejected.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

__all__ = ["UnsafeXml", "fromstring", "safe_parser", "scan_for_unsafe"]


class UnsafeXml(ValueError):
    """The document contains a DTD construct we refuse to process."""


#: Only the prolog can carry a DTD, but a document can be padded, so the scan
#: covers a generous window.
_PROLOG_WINDOW = 128 * 1024

_ENTITY_DECL_RE = re.compile(rb"<!\s*ENTITY\b", re.I)
_DOCTYPE_RE = re.compile(rb"<!\s*DOCTYPE\b", re.I)
#: An internal subset -- the "[ ... ]" after the doctype name -- is where
#: entity bombs live. A bare DOCTYPE with no subset is harmless.
_INTERNAL_SUBSET_RE = re.compile(rb"<!\s*DOCTYPE\b[^>\[]*\[", re.I)


def scan_for_unsafe(data: bytes) -> str | None:
    """Return a reason if ``data`` carries a dangerous DTD construct."""
    window = data[:_PROLOG_WINDOW]
    if _ENTITY_DECL_RE.search(window):
        return "an <!ENTITY> declaration"
    if _INTERNAL_SUBSET_RE.search(window):
        return "a DOCTYPE internal subset"
    if _DOCTYPE_RE.search(window) and b"SYSTEM" in window.upper():
        return "a DOCTYPE with an external identifier"
    return None


def safe_parser() -> ET.XMLParser:
    """A plain XMLParser. Safety comes from :func:`scan_for_unsafe`."""
    # S314: bare ElementTree is only reached through fromstring(), which
    # rejects entity and DTD constructs first. Callers that bypass that guard
    # are opting out deliberately.
    return ET.XMLParser()  # noqa: S314


def fromstring(data: bytes) -> ET.Element:
    """Parse ``data`` into an Element, refusing dangerous DTD constructs.

    Raises :class:`UnsafeXml` when the document declares entities, and
    ``ET.ParseError`` for ordinary malformed XML.
    """
    reason = scan_for_unsafe(data)
    if reason is not None:
        raise UnsafeXml(f"refusing XML containing {reason}")
    parser = safe_parser()
    parser.feed(data)
    return parser.close()
