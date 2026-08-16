"""Codepoint classification tables for Layer A (invisible-character watermarks).

Pure stdlib. No I/O. Everything here is a deterministic function of the input
string, so inspect and clean always agree on what is present.

Three disjoint decisions are made per character:

  1. Is it a *strip candidate*?  (invisible / formatting / private-use)
  2. Is it a *space homoglyph*?  (visible whitespace that is not U+0020)
  3. Is it a *Latin confusable*? (aggressive mode only)

A strip candidate is only actually removed if it is not load-bearing in its
context -- see ``is_load_bearing``. Some invisible characters do real
typographic work (emoji ZWJ sequences, Persian/Devanagari joiners, Mongolian
free variation selectors) and removing them corrupts the text.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from typing import NamedTuple

# --------------------------------------------------------------------------
# Range helpers
# --------------------------------------------------------------------------

Range = tuple[int, int]


def _in(cp: int, ranges: Iterable[Range]) -> bool:
    return any(lo <= cp <= hi for lo, hi in ranges)


# --------------------------------------------------------------------------
# Kind labels (stable strings -- the HTTP API and UI depend on these)
# --------------------------------------------------------------------------

KIND_STRIP = "strip"
KIND_BIDI = "bidi"
KIND_TAG = "tag_chars"
KIND_VS = "variation_selector"
KIND_ZWJ = "zwj_family"
KIND_PUA = "private_use"
KIND_SPACE = "space"
KIND_CONFUSABLE = "confusable"
KIND_OTHER_CF = "other_cf"

ALL_KINDS = (
    KIND_STRIP,
    KIND_BIDI,
    KIND_TAG,
    KIND_VS,
    KIND_ZWJ,
    KIND_PUA,
    KIND_SPACE,
    KIND_CONFUSABLE,
    KIND_OTHER_CF,
)

# --------------------------------------------------------------------------
# Strip sets
# --------------------------------------------------------------------------

# Zero-width space family + directional marks. U+200B..U+200F.
ZWJ_FAMILY: tuple[Range, ...] = ((0x200B, 0x200F),)

# Explicit bidirectional formatting. Overrides + isolates.
BIDI: tuple[Range, ...] = (
    (0x061C, 0x061C),  # Arabic letter mark
    (0x202A, 0x202E),  # LRE RLE PDF LRO RLO
    (0x2066, 0x2069),  # LRI RLI FSI PDI
)

# Word joiner and the invisible math operators.
INVISIBLE_OPERATORS: tuple[Range, ...] = ((0x2060, 0x2064),)

# Deprecated format characters (inhibit/activate symmetric swapping etc).
DEPRECATED_FORMAT: tuple[Range, ...] = ((0x206A, 0x206F),)

# Interlinear annotation anchors -- never legitimate in plain text.
INTERLINEAR: tuple[Range, ...] = ((0xFFF9, 0xFFFB),)

# Variation selectors. VS1-16 in the BMP, VS17-256 in the supplement.
# The supplementary block is the one used by the "hidden text in emoji" trick.
VARIATION_SELECTORS: tuple[Range, ...] = (
    (0xFE00, 0xFE0F),
    (0xE0100, 0xE01EF),
)

# Tag characters. U+E0001 is the (deprecated) language tag; U+E0020..E007F are
# the ASCII-mirroring tags used both for subdivision flags and for smuggling
# arbitrary ASCII into a string invisibly.
TAG_CHARS: tuple[Range, ...] = (
    (0xE0001, 0xE0001),
    (0xE0020, 0xE007F),
)

# Private use areas. Nothing standards-based lives here.
PRIVATE_USE: tuple[Range, ...] = (
    (0xE000, 0xF8FF),
    (0xF0000, 0xFFFFD),
    (0x100000, 0x10FFFD),
)

# Assorted single codepoints that are invisible or near-invisible.
MISC_STRIP: tuple[Range, ...] = (
    (0x00AD, 0x00AD),  # soft hyphen
    (0x034F, 0x034F),  # combining grapheme joiner
    (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
)

# --------------------------------------------------------------------------
# Space homoglyphs -> U+0020
# --------------------------------------------------------------------------

SPACE_HOMOGLYPHS: tuple[Range, ...] = (
    (0x00A0, 0x00A0),  # no-break space
    (0x1680, 0x1680),  # ogham space mark
    (0x2000, 0x200A),  # en quad .. hair space
    (0x202F, 0x202F),  # narrow no-break space
    (0x205F, 0x205F),  # medium mathematical space
    (0x3000, 0x3000),  # ideographic space
)

# --------------------------------------------------------------------------
# Latin confusables (aggressive mode only -- these are visible characters and
# stripping them changes text that may legitimately be Cyrillic or fullwidth)
# --------------------------------------------------------------------------

# fmt: off
CYRILLIC_CONFUSABLES: dict[int, str] = {
    0x0410: "A", 0x0412: "B", 0x0415: "E", 0x041A: "K", 0x041C: "M",
    0x041D: "H", 0x041E: "O", 0x0420: "P", 0x0421: "C", 0x0422: "T",
    0x0423: "Y", 0x0425: "X", 0x0405: "S", 0x0406: "I", 0x0408: "J",
    0x04AE: "Y", 0x04C0: "I",
    0x0430: "a", 0x0435: "e", 0x043E: "o", 0x0440: "p", 0x0441: "c",
    0x0443: "y", 0x0445: "x", 0x0455: "s", 0x0456: "i", 0x0458: "j",
    0x04CF: "l",
}

GREEK_CONFUSABLES: dict[int, str] = {
    0x0391: "A", 0x0392: "B", 0x0395: "E", 0x0396: "Z", 0x0397: "H",
    0x0399: "I", 0x039A: "K", 0x039C: "M", 0x039D: "N", 0x039F: "O",
    0x03A1: "P", 0x03A4: "T", 0x03A5: "Y", 0x03A7: "X", 0x03BF: "o",
}
# fmt: on


def _fullwidth_map() -> dict[int, str]:
    out: dict[int, str] = {}
    for i in range(26):
        out[0xFF21 + i] = chr(ord("A") + i)
        out[0xFF41 + i] = chr(ord("a") + i)
    for i in range(10):
        out[0xFF10 + i] = chr(ord("0") + i)
    return out


FULLWIDTH_CONFUSABLES: dict[int, str] = _fullwidth_map()

CONFUSABLES: dict[int, str] = {
    **CYRILLIC_CONFUSABLES,
    **GREEK_CONFUSABLES,
    **FULLWIDTH_CONFUSABLES,
}

# --------------------------------------------------------------------------
# Always keep -- orthographic format characters that carry meaning
# --------------------------------------------------------------------------

ALWAYS_KEEP: tuple[Range, ...] = (
    (0x0600, 0x0605),  # Arabic number signs
    (0x06DD, 0x06DD),  # Arabic end of ayah
    (0x070F, 0x070F),  # Syriac abbreviation mark
    (0x08E2, 0x08E2),  # Arabic disputed end of ayah
    (0x110BD, 0x110BD),  # Kaithi number sign
    (0x110CD, 0x110CD),  # Kaithi number sign above
)

# --------------------------------------------------------------------------
# Context ranges used by the load-bearing check
# --------------------------------------------------------------------------

EMOJI_BASE: tuple[Range, ...] = (
    (0x00A9, 0x00A9),
    (0x00AE, 0x00AE),
    (0x203C, 0x203C),
    (0x2049, 0x2049),
    (0x2122, 0x2122),
    (0x2139, 0x2139),
    (0x2190, 0x21FF),
    (0x2300, 0x23FF),
    (0x25A0, 0x27BF),
    (0x2934, 0x2935),
    (0x2B00, 0x2BFF),
    (0x3030, 0x3030),
    (0x303D, 0x303D),
    (0x3297, 0x3297),
    (0x3299, 0x3299),
    (0x1F000, 0x1FAFF),
)

REGIONAL_INDICATORS: Range = (0x1F1E6, 0x1F1FF)

MONGOLIAN_FVS: Range = (0x180B, 0x180D)
MONGOLIAN_LETTERS: Range = (0x1800, 0x18AF)

KHMER_VOWEL_INHERENT: Range = (0x17B4, 0x17B5)
KHMER_BLOCK: Range = (0x1780, 0x17FF)

HANGUL_FILLERS: Range = (0x115F, 0x1160)
HANGUL_JAMO: tuple[Range, ...] = (
    (0x1100, 0x11FF),
    (0xA960, 0xA97C),
    (0xD7B0, 0xD7C6),
)


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class Classification(NamedTuple):
    """What we think a single codepoint is."""

    kind: str
    """One of ALL_KINDS."""

    replacement: str
    """What to substitute. Empty string means delete."""

    confidence: str
    """``probable`` for invisibles, ``informational`` for spaces/confusables."""


def classify(cp: int, aggressive: bool = False) -> Classification | None:
    """Classify a codepoint, or return None if it is unremarkable."""
    if _in(cp, ALWAYS_KEEP):
        return None

    if _in(cp, SPACE_HOMOGLYPHS):
        return Classification(KIND_SPACE, " ", "informational")

    if _in(cp, ZWJ_FAMILY):
        # U+200E and U+200F are directional marks, so label them bidi and let
        # the report say so.
        kind = KIND_BIDI if cp in (0x200E, 0x200F) else KIND_ZWJ
        return Classification(kind, "", "probable")

    if _in(cp, BIDI):
        return Classification(KIND_BIDI, "", "probable")

    if _in(cp, TAG_CHARS):
        return Classification(KIND_TAG, "", "probable")

    if _in(cp, VARIATION_SELECTORS):
        return Classification(KIND_VS, "", "probable")

    if _in(cp, PRIVATE_USE):
        return Classification(KIND_PUA, "", "probable")

    if _in(cp, INVISIBLE_OPERATORS) or _in(cp, DEPRECATED_FORMAT):
        return Classification(KIND_OTHER_CF, "", "probable")

    if _in(cp, INTERLINEAR) or _in(cp, MISC_STRIP):
        return Classification(KIND_STRIP, "", "probable")

    if aggressive and cp in CONFUSABLES:
        return Classification(KIND_CONFUSABLE, CONFUSABLES[cp], "informational")

    # Catch-all: any other format character we did not enumerate.
    if unicodedata.category(chr(cp)) == "Cf":
        return Classification(KIND_OTHER_CF, "", "probable")

    return None


# --------------------------------------------------------------------------
# Load-bearing detection
# --------------------------------------------------------------------------


def _is_emoji_base(ch: str | None) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return _in(cp, EMOJI_BASE) or (REGIONAL_INDICATORS[0] <= cp <= REGIONAL_INDICATORS[1])


def is_load_bearing(text: str, index: int) -> str | None:
    """Return a reason string if ``text[index]`` is doing real work, else None.

    ``index`` must point at a character that :func:`classify` flagged. The
    decision looks at the previous *kept-relevant* character, which for our
    purposes is simply the preceding character in the source string -- emoji
    sequences and script joiners are always immediately adjacent to their base.
    """
    cp = ord(text[index])
    prev = text[index - 1] if index > 0 else None
    prev_cp = ord(prev) if prev else None

    # Emoji glue: ZWJ and VS15/VS16 directly after an emoji base are part of a
    # single grapheme (family emoji, skin tones, text/emoji presentation).
    if cp in (0x200D, 0xFE0E, 0xFE0F) and _is_emoji_base(prev):
        return "emoji_sequence"

    # Tag-sequence flags (the Scotland flag is a black-flag base followed by
    # tag characters and a cancel tag). Walk back over the whole tag run to
    # find what it is anchored to: a run is only legitimate if it *starts*
    # after an emoji base. Without this walk-back, arbitrary smuggled tag text
    # would look load-bearing from its second character onward, because each
    # tag's predecessor is itself a tag.
    if _in(cp, TAG_CHARS):
        j = index - 1
        while j >= 0 and _in(ord(text[j]), TAG_CHARS):
            j -= 1
        if j >= 0 and _is_emoji_base(text[j]):
            return "flag_tag_sequence"

    # Script joiners: ZWNJ/ZWJ after a non-ASCII letter or mark is orthographic
    # in Persian, Arabic, Devanagari, Bengali, and similar scripts.
    if (
        cp in (0x200C, 0x200D)
        and prev_cp is not None
        and prev_cp > 0x7F
        and unicodedata.category(prev)[0] in ("L", "M")
    ):
        return "script_joiner"

    # Mongolian free variation selectors follow Mongolian letters.
    if (
        MONGOLIAN_FVS[0] <= cp <= MONGOLIAN_FVS[1]
        and prev_cp is not None
        and MONGOLIAN_LETTERS[0] <= prev_cp <= MONGOLIAN_LETTERS[1]
    ):
        return "mongolian_fvs"

    # Khmer inherent vowels follow Khmer consonants.
    if (
        KHMER_VOWEL_INHERENT[0] <= cp <= KHMER_VOWEL_INHERENT[1]
        and prev_cp
        and KHMER_BLOCK[0] <= prev_cp <= KHMER_BLOCK[1]
    ):
        return "khmer_inherent_vowel"

    # Hangul jamo fillers are only meaningful inside a jamo cluster.
    if (
        HANGUL_FILLERS[0] <= cp <= HANGUL_FILLERS[1]
        and prev_cp is not None
        and _in(prev_cp, HANGUL_JAMO)
    ):
        return "hangul_jamo_filler"

    return None


def codepoint_name(cp: int) -> str:
    try:
        return unicodedata.name(chr(cp))
    except ValueError:
        if _in(cp, PRIVATE_USE):
            return "<private-use>"
        if _in(cp, TAG_CHARS):
            return "<tag character>"
        return "<unnamed>"
