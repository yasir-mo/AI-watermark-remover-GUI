"""wmtk -- AI watermark removal toolkit.

Three layers:
  A  invisible Unicode characters embedded in text (deterministic, lossless)
  B  statistical token-sampling watermarks (probabilistic, via LLM rewrite)
  C  file metadata: C2PA, EXIF, XMP, document properties (deterministic)

Core is stdlib-only. Optional external tools (exiftool, c2patool, qpdf) and
heavy ML backends (MarkLLM, SynthID, CtrlRegen) are detected at runtime and
never required.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
