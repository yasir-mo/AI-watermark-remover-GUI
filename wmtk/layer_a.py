"""Layer A engine: scan and clean invisible-character watermarks in text.

Deterministic, stdlib-only, no network. ``scan`` and ``clean`` share the same
classification pass so a report always matches what a clean would do.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .unicode_tables import (
    KIND_CONFUSABLE,
    KIND_SPACE,
    classify,
    codepoint_name,
    is_load_bearing,
)

MAX_OFFSETS_REPORTED = 10


@dataclass
class Finding:
    """One distinct codepoint that was found, aggregated over the whole text."""

    codepoint: int
    name: str
    category: str
    kind: str
    count: int = 0
    offsets: list[int] = field(default_factory=list)
    kept_count: int = 0
    kept_reasons: dict[str, int] = field(default_factory=dict)
    replacement: str = ""
    confidence: str = "probable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "codepoint": f"U+{self.codepoint:04X}",
            "codepoint_int": self.codepoint,
            "name": self.name,
            "category": self.category,
            "kind": self.kind,
            "count": self.count,
            "offsets": self.offsets[:MAX_OFFSETS_REPORTED],
            "offsets_truncated": len(self.offsets) > MAX_OFFSETS_REPORTED,
            "kept_as_load_bearing": self.kept_count,
            "kept_reasons": self.kept_reasons,
            "replacement": self.replacement,
            "confidence": self.confidence,
        }


@dataclass
class ScanResult:
    findings: list[Finding]
    length: int
    aggressive: bool
    strip_emoji_glue: bool

    @property
    def total_flagged(self) -> int:
        return sum(f.count for f in self.findings)

    @property
    def total_removable(self) -> int:
        return sum(f.count - f.kept_count for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for f in self.findings:
            by_kind[f.kind] = by_kind.get(f.kind, 0) + (f.count - f.kept_count)
        return {
            "layer": "A",
            "input_length": self.length,
            "aggressive": self.aggressive,
            "strip_emoji_glue": self.strip_emoji_glue,
            "total_flagged": self.total_flagged,
            "total_removable": self.total_removable,
            "by_kind": by_kind,
            "watermark_suspected": any(
                f.confidence == "probable" and f.count > f.kept_count for f in self.findings
            ),
            "findings": [f.to_dict() for f in self.findings],
        }


def scan(
    text: str,
    *,
    aggressive: bool = False,
    strip_emoji_glue: bool = False,
) -> ScanResult:
    """Classify every character and aggregate per codepoint."""
    found: dict[int, Finding] = {}

    for i, ch in enumerate(text):
        cp = ord(ch)
        # Fast path: plain ASCII printable text is by far the common case.
        if 0x20 <= cp < 0x7F:
            continue

        cls = classify(cp, aggressive=aggressive)
        if cls is None:
            continue

        f = found.get(cp)
        if f is None:
            f = Finding(
                codepoint=cp,
                name=codepoint_name(cp),
                category=unicodedata.category(ch),
                kind=cls.kind,
                replacement=cls.replacement,
                confidence=cls.confidence,
            )
            found[cp] = f

        f.count += 1
        if len(f.offsets) < MAX_OFFSETS_REPORTED * 4:
            f.offsets.append(i)

        if not strip_emoji_glue and cls.kind not in (KIND_SPACE, KIND_CONFUSABLE):
            reason = is_load_bearing(text, i)
            if reason:
                f.kept_count += 1
                f.kept_reasons[reason] = f.kept_reasons.get(reason, 0) + 1

    findings = sorted(found.values(), key=lambda f: (-f.count, f.codepoint))
    return ScanResult(
        findings=findings,
        length=len(text),
        aggressive=aggressive,
        strip_emoji_glue=strip_emoji_glue,
    )


@dataclass
class CleanResult:
    text: str
    input_length: int
    output_length: int
    removed: dict[str, int]
    replaced: dict[str, int]
    kept: dict[str, int]
    normalized: bool

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())

    @property
    def total_replaced(self) -> int:
        return sum(self.replaced.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "A",
            "input_length": self.input_length,
            "output_length": self.output_length,
            "total_removed": self.total_removed,
            "total_replaced": self.total_replaced,
            "removed": self.removed,
            "replaced": self.replaced,
            "kept_load_bearing": self.kept,
            "nfkc_normalized": self.normalized,
        }


def clean(
    text: str,
    *,
    aggressive: bool = False,
    strip_emoji_glue: bool = False,
    normalize: bool = False,
) -> CleanResult:
    """Remove or replace flagged characters, preserving load-bearing ones."""
    out: list[str] = []
    removed: dict[str, int] = {}
    replaced: dict[str, int] = {}
    kept: dict[str, int] = {}

    def bump(d: dict[str, int], key: str) -> None:
        d[key] = d.get(key, 0) + 1

    for i, ch in enumerate(text):
        cp = ord(ch)
        if 0x20 <= cp < 0x7F:
            out.append(ch)
            continue

        cls = classify(cp, aggressive=aggressive)
        if cls is None:
            out.append(ch)
            continue

        if not strip_emoji_glue and cls.kind not in (KIND_SPACE, KIND_CONFUSABLE):
            reason = is_load_bearing(text, i)
            if reason:
                bump(kept, reason)
                out.append(ch)
                continue

        label = f"U+{cp:04X} {codepoint_name(cp)}"
        if cls.replacement:
            bump(replaced, label)
            out.append(cls.replacement)
        else:
            bump(removed, label)

    result = "".join(out)
    if normalize:
        result = unicodedata.normalize("NFKC", result)

    return CleanResult(
        text=result,
        input_length=len(text),
        output_length=len(result),
        removed=removed,
        replaced=replaced,
        kept=kept,
        normalized=normalize,
    )


def diff_ops(original: str, cleaned: str) -> list[dict[str, Any]]:
    """Character-level diff for the UI, as a list of ``{op, text}`` runs.

    Uses :class:`difflib.SequenceMatcher` so the UI can highlight exactly what
    disappeared without re-running the classifier client-side.
    """
    from difflib import SequenceMatcher

    ops: list[dict[str, Any]] = []
    matcher = SequenceMatcher(None, original, cleaned, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            ops.append({"op": "equal", "text": original[i1:i2]})
        elif tag == "delete":
            ops.append({"op": "delete", "text": original[i1:i2]})
        elif tag == "insert":
            ops.append({"op": "insert", "text": cleaned[j1:j2]})
        else:
            ops.append({"op": "replace", "text": original[i1:i2], "with": cleaned[j1:j2]})
    return ops
