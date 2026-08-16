"""Generate the web build's Unicode tables and conformance vectors.

Layer A exists twice: in Python (CLI, local service) and in JavaScript (the
static demo site). Two hand-maintained copies of watermark-stripping logic
would drift, and the failure is silent -- the web version would tell someone
their text is clean when the CLI would still find marks in it.

So the tables are generated from ``wmtk.unicode_tables`` rather than retyped,
and both runtimes are pinned to the same conformance vectors. CI regenerates
and diffs; a drift turns the build red instead of shipping.

    python -m tools.gen_web_assets           # write the files
    python -m tools.gen_web_assets --check   # fail if they are stale
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wmtk import __version__
from wmtk.layer_a import clean, scan
from wmtk.unicode_tables import (
    ALWAYS_KEEP,
    BIDI,
    CONFUSABLES,
    DEPRECATED_FORMAT,
    EMOJI_BASE,
    HANGUL_FILLERS,
    HANGUL_JAMO,
    INTERLINEAR,
    INVISIBLE_OPERATORS,
    KHMER_BLOCK,
    KHMER_VOWEL_INHERENT,
    MISC_STRIP,
    MONGOLIAN_FVS,
    MONGOLIAN_LETTERS,
    PRIVATE_USE,
    REGIONAL_INDICATORS,
    SPACE_HOMOGLYPHS,
    TAG_CHARS,
    VARIATION_SELECTORS,
    ZWJ_FAMILY,
    codepoint_name,
)

ROOT = Path(__file__).resolve().parent.parent
TABLES_JS = ROOT / "web" / "src" / "tables.js"
VECTORS_JSON = ROOT / "conformance" / "vectors.json"

BANNER = (
    "// GENERATED FILE -- DO NOT EDIT.\n"
    "// Source: wmtk/unicode_tables.py\n"
    "// Regenerate: python -m tools.gen_web_assets\n"
)


def _ranges(name: str, ranges: tuple[tuple[int, int], ...]) -> str:
    body = ", ".join(f"[0x{lo:X},0x{hi:X}]" for lo, hi in ranges)
    return f"export const {name} = [{body}];\n"


def _pair(name: str, pair: tuple[int, int]) -> str:
    return f"export const {name} = [0x{pair[0]:X},0x{pair[1]:X}];\n"


def build_tables_js() -> str:
    out = [BANNER, "\n"]
    out.append(f'export const SOURCE_VERSION = "{__version__}";\n\n')

    out.append(_ranges("ZWJ_FAMILY", ZWJ_FAMILY))
    out.append(_ranges("BIDI", BIDI))
    out.append(_ranges("INVISIBLE_OPERATORS", INVISIBLE_OPERATORS))
    out.append(_ranges("DEPRECATED_FORMAT", DEPRECATED_FORMAT))
    out.append(_ranges("INTERLINEAR", INTERLINEAR))
    out.append(_ranges("VARIATION_SELECTORS", VARIATION_SELECTORS))
    out.append(_ranges("TAG_CHARS", TAG_CHARS))
    out.append(_ranges("PRIVATE_USE", PRIVATE_USE))
    out.append(_ranges("MISC_STRIP", MISC_STRIP))
    out.append(_ranges("SPACE_HOMOGLYPHS", SPACE_HOMOGLYPHS))
    out.append(_ranges("ALWAYS_KEEP", ALWAYS_KEEP))
    out.append(_ranges("EMOJI_BASE", EMOJI_BASE))
    out.append(_ranges("HANGUL_JAMO", HANGUL_JAMO))
    out.append("\n")

    out.append(_pair("REGIONAL_INDICATORS", REGIONAL_INDICATORS))
    out.append(_pair("MONGOLIAN_FVS", MONGOLIAN_FVS))
    out.append(_pair("MONGOLIAN_LETTERS", MONGOLIAN_LETTERS))
    out.append(_pair("KHMER_VOWEL_INHERENT", KHMER_VOWEL_INHERENT))
    out.append(_pair("KHMER_BLOCK", KHMER_BLOCK))
    out.append(_pair("HANGUL_FILLERS", HANGUL_FILLERS))
    out.append("\n")

    confusables = ", ".join(
        f"[0x{cp:X},{json.dumps(rep)}]" for cp, rep in sorted(CONFUSABLES.items())
    )
    out.append(f"export const CONFUSABLES = new Map([{confusables}]);\n\n")

    # Unicode names, so the browser can label a finding without shipping the
    # whole UCD.
    #
    # This must cover EVERY codepoint classify() can flag, not just the
    # enumerated ranges. The catch-all branch flags any Cf character, so a
    # codepoint like U+180E MONGOLIAN VOWEL SEPARATOR would otherwise show as
    # "<unnamed>" in the browser while Python reports its real name -- a
    # divergence between the two implementations, which is exactly what this
    # generator exists to prevent. Scanning the Cf category costs ~170 entries.
    names: dict[str, str] = {}

    for cp in range(0x110000):
        if unicodedata.category(chr(cp)) == "Cf":
            names[f"{cp:X}"] = codepoint_name(cp)

    # Confusables are visible letters, not Cf, so the sweep above misses them.
    # Aggressive mode flags them, and without this every one displayed as
    # "<unnamed>" in the browser while the CLI named it properly.
    for cp in CONFUSABLES:
        names[f"{cp:X}"] = codepoint_name(cp)

    for lo, hi in (
        *ZWJ_FAMILY,
        *BIDI,
        *INVISIBLE_OPERATORS,
        *DEPRECATED_FORMAT,
        *INTERLINEAR,
        *MISC_STRIP,
        *SPACE_HOMOGLYPHS,
        (0xFE00, 0xFE0F),
        (0xE0001, 0xE0001),
    ):
        for cp in range(lo, hi + 1):
            names[f"{cp:X}"] = codepoint_name(cp)
    out.append(
        "export const NAMES = "
        + json.dumps(names, indent=0, sort_keys=True).replace("\n", "")
        + ";\n"
    )
    return "".join(out)


# --------------------------------------------------------------------------
# Conformance vectors
# --------------------------------------------------------------------------

#: Each case is (id, text, options). Expected output is computed by the Python
#: implementation, which is the reference. The JS runner must reproduce it.
CASES: list[tuple[str, str, dict]] = [
    ("empty", "", {}),
    ("plain-ascii", "The quick brown fox jumps over the lazy dog.", {}),
    ("zwsp", "hello​world", {}),
    ("zwsp-many", "a​b​c​d", {}),
    ("word-joiner", "he⁠llo", {}),
    ("bom", "﻿leading bom", {}),
    ("soft-hyphen", "soft­hyphen", {}),
    ("cgj", "a͏b", {}),
    ("rlo", "before‮after", {}),
    ("bidi-isolates", "x⁦y⁩z", {}),
    ("arabic-letter-mark", "a؜b", {}),
    ("nbsp", "a b", {}),
    ("all-spaces", "a b c d e　f g h i", {}),
    ("private-use", "abc", {}),
    ("private-use-supplementary", "a\U000f0000b", {}),
    ("variation-selector-standalone", "x️y", {}),
    ("variation-selector-supplementary", "x\U000e0100y", {}),
    ("interlinear", "a￹b￺c￻d", {}),
    ("deprecated-format", "a⁪b⁯c", {}),
    # Hits the catch-all Cf branch rather than an enumerated range, which is
    # where the JS name table used to fall back to "<unnamed>".
    ("mongolian-vowel-separator", "a᠎b", {}),
    # Load-bearing: these must survive untouched.
    ("emoji-zwj-family", "\U0001f469‍\U0001f467", {}),
    ("emoji-zwj-long", "\U0001f468‍\U0001f469‍\U0001f466", {}),
    ("emoji-vs16", "❤️", {}),
    ("persian-zwnj", "پ‌ر", {}),
    ("devanagari-zwj", "क‍ष", {}),
    ("mongolian-fvs", "ᠠ᠋", {}),
    ("khmer-inherent", "ក឴", {}),
    (
        "flag-tag-sequence",
        "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f",
        {},
    ),
    ("orthographic-arabic", "؀؁ text ۝", {}),
    # Adversarial: a tag run not anchored to an emoji must be stripped whole.
    (
        "smuggled-tag-payload",
        "Hello\U000e0053\U000e0045\U000e0043\U000e0052\U000e0045\U000e0054",
        {},
    ),
    ("tag-after-letter", "A\U000e0041B", {}),
    # Mixed
    ("mixed-real-world", "Draft​ report with nbsp and \U0001f469‍\U0001f467 emoji.", {}),
    ("mixed-with-newlines", "line one​\nline two\n\nline three⁠", {}),
    # Options
    ("aggressive-cyrillic", "Bank of Amеrica", {"aggressive": True}),
    ("aggressive-fullwidth", "Ｈｅｌｌｏ", {"aggressive": True}),
    ("aggressive-off-cyrillic", "Bank of Amеrica", {}),
    ("strip-glue-emoji", "\U0001f469‍\U0001f467", {"strip_emoji_glue": True}),
    ("strip-glue-persian", "پ‌ر", {"strip_emoji_glue": True}),
    ("normalize-ligature", "ﬁve", {"normalize": True}),
    ("normalize-off", "ﬁve", {}),
    # Non-Latin bodies that must be untouched
    ("chinese", "中文测试内容", {}),
    ("arabic-body", "مرحبا بالعالم", {}),
    ("emoji-plain", "\U0001f600 \U0001f680 \U0001f31f", {}),
    ("surrogate-pair-text", "\U0001d400\U0001d401\U0001d402", {}),
]


def build_vectors() -> dict:
    cases = []
    for case_id, text, opts in CASES:
        scanned = scan(
            text,
            aggressive=opts.get("aggressive", False),
            strip_emoji_glue=opts.get("strip_emoji_glue", False),
        )
        cleaned = clean(
            text,
            aggressive=opts.get("aggressive", False),
            strip_emoji_glue=opts.get("strip_emoji_glue", False),
            normalize=opts.get("normalize", False),
        )
        cases.append(
            {
                "id": case_id,
                "input": text,
                "options": {
                    "aggressive": opts.get("aggressive", False),
                    "strip_emoji_glue": opts.get("strip_emoji_glue", False),
                    "normalize": opts.get("normalize", False),
                },
                "expect": {
                    "cleaned": cleaned.text,
                    "total_removed": cleaned.total_removed,
                    "total_replaced": cleaned.total_replaced,
                    "kept_load_bearing": cleaned.kept,
                    "total_flagged": scanned.total_flagged,
                    "total_removable": scanned.total_removable,
                    "watermark_suspected": scanned.to_dict()["watermark_suspected"],
                    "findings": [
                        {
                            # Match Finding.to_dict(): the wire form is the
                            # "U+XXXX" string, not the raw integer.
                            "codepoint": f"U+{f.codepoint:04X}",
                            "name": f.name,
                            "kind": f.kind,
                            "count": f.count,
                            "kept": f.kept_count,
                        }
                        for f in scanned.findings
                    ],
                },
            }
        )
    return {
        "note": (
            "GENERATED by tools/gen_web_assets.py from the Python implementation, "
            "which is the reference. Both wmtk (Python) and web/src/layer-a.js "
            "must reproduce every expectation here."
        ),
        "source_version": __version__,
        "count": len(cases),
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gen_web_assets")
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the generated files are stale",
    )
    args = ap.parse_args(argv)

    tables = build_tables_js()
    vectors = json.dumps(build_vectors(), indent=2, ensure_ascii=False) + "\n"

    targets = [(TABLES_JS, tables), (VECTORS_JSON, vectors)]

    if args.check:
        stale = []
        for path, content in targets:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(path.relative_to(ROOT).as_posix())
        if stale:
            print(
                "error: generated assets are stale: "
                + ", ".join(stale)
                + "\n       run: python -m tools.gen_web_assets",
                file=sys.stderr,
            )
            return 1
        print(f"generated assets are current ({len(CASES)} conformance cases)")
        return 0

    for path, content in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT).as_posix()}")
    print(f"{len(CASES)} conformance cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
