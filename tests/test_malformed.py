"""Corrupt input: every format either cleans or refuses, never crashes or lies.

The fuzz loop is seeded and small enough to run in CI. Before MalformedFile
existed, a few hundred mutations per fixture surfaced BadZipFile, zlib.error,
NotImplementedError and RuntimeError escaping from zipfile, ValueError from the
PNG chunk walker, and a DOCX path that returned the untouched input as clean.
"""

from __future__ import annotations

import base64
import contextlib
import random
import re
import urllib.error

import pytest

from scrubai.cli import clean_file as clean_file_cli
from scrubai.metadata import MalformedFile, clean_file, inspect_file, ooxml, pdf
from tests.test_server import server  # noqa: F401  (module-scoped fixture)

FIXTURES = {
    "png_with_metadata": "a.png",
    "jpeg_with_metadata": "a.jpg",
    "webp_with_metadata": "a.webp",
    "docx_with_properties": "a.docx",
    "odt_with_properties": "a.odt",
    "pdf_with_metadata": "a.pdf",
    "svg_with_metadata": "a.svg",
    "html_with_metadata": "a.html",
    "markdown_with_frontmatter": "a.md",
}


def _mutate(rng: random.Random, b: bytes) -> bytes:
    buf = bytearray(b)
    op = rng.randrange(4)
    if op == 0:
        return bytes(buf[: rng.randrange(1, len(buf))])
    if op == 1:
        for _ in range(rng.randint(1, 8)):
            buf[rng.randrange(len(buf))] = rng.randrange(256)
        return bytes(buf)
    i = rng.randrange(len(buf))
    if op == 2:
        return bytes(buf[:i] + buf[i : i + 50] * rng.randint(2, 20) + buf[i:])
    return bytes(buf[:i] + buf[i + rng.randint(1, 40) :])


@pytest.mark.parametrize("fixture,name", FIXTURES.items())
def test_mutated_input_cleans_or_refuses(fixture, name, request, monkeypatch):
    # Keep the run hermetic and fast: no exiftool/qpdf even if installed.
    monkeypatch.setattr(pdf, "have_tool", lambda _n: False)
    monkeypatch.setattr("scrubai.metadata.have_tool", lambda _n: False)
    seed = request.getfixturevalue(fixture)
    rng = random.Random(fixture)
    for _ in range(300):
        data = _mutate(rng, seed)
        inspect_file(data, name)  # must never raise
        with contextlib.suppress(MalformedFile):
            clean_file(data, name)


def test_corrupt_docx_is_refused_not_passed_through(docx_with_properties):
    corrupt = docx_with_properties[:-30]  # cut the central directory
    with pytest.raises(MalformedFile):
        ooxml.clean(corrupt)
    report = inspect_file(corrupt, "a.docx", use_tools=False)
    assert report.warnings


def test_server_returns_422_for_malformed_file(server, png_with_metadata):  # noqa: F811
    corrupt = png_with_metadata[:40]
    with pytest.raises(urllib.error.HTTPError) as exc:
        server.post("/clean", {"file": base64.b64encode(corrupt).decode(), "name": "a.png"})
    assert exc.value.code == 422


def test_server_rejects_bad_rewrite_numbers_with_400(server):  # noqa: F811
    with pytest.raises(urllib.error.HTTPError) as exc:
        server.post("/rewrite", {"text": "hi", "temperature": "hot"})
    assert exc.value.code == 400


def test_cli_refuses_and_writes_nothing(tmp_path, png_with_metadata, capsys):
    src = tmp_path / "a.png"
    src.write_bytes(png_with_metadata[:40])
    assert clean_file_cli.main([str(src)]) == 2
    assert not (tmp_path / "a.cleaned.png").exists()
    assert "nothing written" in capsys.readouterr().err


def test_pdf_stdlib_clean_preserves_offsets(pdf_with_metadata, monkeypatch):
    monkeypatch.setattr(pdf, "have_tool", lambda _n: False)
    out, result = pdf.clean(pdf_with_metadata)
    assert len(out) == len(pdf_with_metadata)
    # Every object still starts at the same byte, so the xref stays valid.
    starts = [m.start() for m in re.finditer(rb"\d+ 0 obj", pdf_with_metadata)]
    assert starts == [m.start() for m in re.finditer(rb"\d+ 0 obj", out)]
    assert b"Jane Author" not in out
    assert not any("stale" in w for w in result.warnings)
