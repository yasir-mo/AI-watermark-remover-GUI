"""Shared types for the format-specific metadata handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetaFinding:
    """One piece of metadata detected in a file."""

    kind: str
    """e.g. c2pa, exif, xmp, iptc, icc, docprops, generator, comment."""

    detail: str
    """Human-readable description of what was found."""

    size: int = 0
    """Bytes occupied, when known."""

    confidence: str = "probable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "size": self.size,
            "confidence": self.confidence,
        }


@dataclass
class MetaResult:
    """Outcome of an inspect or clean pass over one file."""

    format: str
    findings: list[MetaFinding] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tier: str = "stdlib"
    """Which implementation path ran: stdlib, exiftool, exiftool+qpdf, ..."""

    def add(self, kind: str, detail: str, size: int = 0, confidence: str = "probable") -> None:
        self.findings.append(MetaFinding(kind, detail, size, confidence))

    def to_dict(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for f in self.findings:
            by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
        return {
            "format": self.format,
            "tier": self.tier,
            "count": len(self.findings),
            "by_kind": by_kind,
            "c2pa_present": any(f.kind == "c2pa" for f in self.findings),
            "findings": [f.to_dict() for f in self.findings],
            "actions": self.actions,
            "warnings": self.warnings,
        }
