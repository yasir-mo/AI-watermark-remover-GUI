"""Metadata handlers: findings are detected, stripped, and the file still parses."""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from tests.conftest import SCAN_BYTES
from wmtk.metadata import detect_format, html_md, jpeg, ooxml, pdf, png, svg, webp


def kinds(result) -> set[str]:
    return {f.kind for f in result.findings}


class TestDetectFormat:
    @pytest.mark.parametrize(
        "name,head,expected",
        [
            ("x.png", b"\x89PNG\r\n\x1a\n", "png"),
            ("x.bin", b"\x89PNG\r\n\x1a\n", "png"),
            ("x.jpg", b"\xff\xd8\xff\xe0", "jpeg"),
            ("x.pdf", b"%PDF-1.7", "pdf"),
            ("x.docx", b"PK\x03\x04", "docx"),
            ("x.odt", b"PK\x03\x04", "odt"),
            ("x.md", b"# hi", "markdown"),
            ("x.txt", b"hi", "text"),
            ("x.html", b"<p>", "html"),
            ("noext", b"<!DOCTYPE html><html>", "html"),
            ("noext", b'<svg xmlns="http://www.w3.org/2000/svg">', "svg"),
        ],
    )
    def test_detection(self, name, head, expected):
        assert detect_format(name, head) == expected

    def test_magic_beats_extension(self):
        """A PNG named .txt is still a PNG."""
        assert detect_format("lies.txt", b"\x89PNG\r\n\x1a\n") == "png"


class TestPng:
    def test_finds_all(self, png_with_metadata):
        assert kinds(png.inspect(png_with_metadata)) >= {"text", "xmp", "c2pa", "exif"}

    def test_clean_removes_all(self, png_with_metadata):
        out, result = png.clean(png_with_metadata)
        assert png.inspect(out).findings == []
        assert result.actions

    def test_keeps_rendering_chunks(self, png_with_metadata):
        out, _ = png.clean(png_with_metadata)
        for chunk in (b"IHDR", b"IDAT", b"IEND", b"gAMA"):
            assert chunk in out, f"{chunk!r} was dropped"

    def test_output_crcs_are_valid(self, png_with_metadata):
        out, _ = png.clean(png_with_metadata)
        png._verify_crcs(out)  # raises on mismatch

    def test_rebuilt_chunk_has_correct_crc(self):
        raw = png.rebuild_chunk(b"tEXt", b"k\x00v")
        assert struct.unpack(">I", raw[:4])[0] == 3
        png._verify_crcs(b"\x89PNG\r\n\x1a\n" + raw + png.rebuild_chunk(b"IEND", b""))

    def test_non_png_is_left_alone(self):
        out, result = png.clean(b"not a png at all")
        assert out == b"not a png at all"
        assert result.warnings


class TestJpeg:
    def test_finds_all(self, jpeg_with_metadata):
        assert kinds(jpeg.inspect(jpeg_with_metadata)) >= {
            "exif",
            "xmp",
            "c2pa",
            "iptc",
            "comment",
        }

    def test_clean_removes_all(self, jpeg_with_metadata):
        out, _ = jpeg.clean(jpeg_with_metadata)
        assert jpeg.inspect(out).findings == []

    def test_structure_survives(self, jpeg_with_metadata):
        out, _ = jpeg.clean(jpeg_with_metadata)
        assert out.startswith(b"\xff\xd8")
        assert out.endswith(b"\xff\xd9")
        assert SCAN_BYTES in out, "entropy-coded scan data was lost"
        assert b"\xff\xdb" in out, "quantization table was lost"
        assert b"\xff\xc0" in out, "frame header was lost"

    def test_smaller_than_input(self, jpeg_with_metadata):
        out, _ = jpeg.clean(jpeg_with_metadata)
        assert len(out) < len(jpeg_with_metadata)


class TestWebp:
    def test_finds_all(self, webp_with_metadata):
        assert kinds(webp.inspect(webp_with_metadata)) >= {"exif", "xmp", "c2pa"}

    def test_clean_removes_all(self, webp_with_metadata):
        out, _ = webp.clean(webp_with_metadata)
        assert webp.inspect(out).findings == []

    def test_riff_size_is_correct(self, webp_with_metadata):
        out, _ = webp.clean(webp_with_metadata)
        declared = struct.unpack("<I", out[4:8])[0]
        assert declared == len(out) - 8

    def test_vp8x_flags_cleared(self, webp_with_metadata):
        out, result = webp.clean(webp_with_metadata)
        idx = out.index(b"VP8X")
        flags = out[idx + 8]
        assert flags & 0b0000_1100 == 0
        assert any("VP8X" in a for a in result.actions)

    def test_image_data_kept(self, webp_with_metadata):
        out, _ = webp.clean(webp_with_metadata)
        assert b"fake-image-data" in out


class TestOoxml:
    def test_finds_properties(self, docx_with_properties):
        found = ooxml.inspect(docx_with_properties)
        assert kinds(found) >= {"docprops", "ai_part", "ai_marker"}
        details = " ".join(f.detail for f in found.findings)
        assert "Jane Author" in details
        assert "ACME Corp" in details

    def test_clean_removes_all(self, docx_with_properties):
        out, _ = ooxml.clean(docx_with_properties)
        assert ooxml.inspect(out).findings == []

    def test_ai_part_dropped_body_kept(self, docx_with_properties):
        out, _ = ooxml.clean(docx_with_properties)
        names = zipfile.ZipFile(io.BytesIO(out)).namelist()
        assert "customXml/item1.xml" not in names
        assert "word/document.xml" in names

    def test_title_is_not_a_provenance_field(self, docx_with_properties):
        """Author and tool go; the document's own title stays."""
        out, _ = ooxml.clean(docx_with_properties)
        core = zipfile.ZipFile(io.BytesIO(out)).read("docProps/core.xml")
        assert b"Quarterly Report" in core
        assert b"Jane Author" not in core

    def test_timestamps_normalized(self, docx_with_properties):
        out, _ = ooxml.clean(docx_with_properties)
        for info in zipfile.ZipFile(io.BytesIO(out)).infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)

    def test_odt_meta_scrubbed(self, odt_with_properties):
        assert ooxml.inspect(odt_with_properties).format == "odt"
        out, _ = ooxml.clean(odt_with_properties)
        meta = zipfile.ZipFile(io.BytesIO(out)).read("meta.xml")
        assert b"LibreOffice" not in meta
        assert b"Jane" not in meta

    def test_odt_mimetype_stays_stored(self, odt_with_properties):
        out, _ = ooxml.clean(odt_with_properties)
        info = zipfile.ZipFile(io.BytesIO(out)).getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED

    def test_zip_bomb_member_count_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i in range(ooxml.MAX_MEMBERS + 1):
                z.writestr(f"f{i}", "x")
        result = ooxml.inspect(buf.getvalue())
        assert any("limit" in w for w in result.warnings)

    def test_unsafe_member_path_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("../../escape.xml", "x")
        assert any("unsafe" in w for w in ooxml.inspect(buf.getvalue()).warnings)


class TestPdf:
    def test_finds_metadata(self, pdf_with_metadata):
        found = pdf.inspect(pdf_with_metadata)
        assert kinds(found) >= {"xmp", "docprops"}
        details = " ".join(f.detail for f in found.findings)
        assert "Jane Author" in details
        assert "ChatGPT" in details

    def test_stdlib_tier_blanks_info_and_warns(self, pdf_with_metadata, monkeypatch):
        monkeypatch.setattr(pdf, "have_tool", lambda name: False)
        out, result = pdf.clean(pdf_with_metadata)
        assert result.tier == "stdlib"
        assert b"Jane Author" not in out
        assert b"ChatGPT" not in out
        assert b"xpacket" not in out
        assert any("degraded" in w for w in result.warnings), (
            "the degraded tier must say so rather than imply a clean removal"
        )

    def test_tier_reported_matches_tools(self, monkeypatch):
        monkeypatch.setattr(pdf, "have_tool", lambda n: n in ("exiftool", "qpdf"))
        assert pdf._available_tier() == "exiftool+qpdf"
        monkeypatch.setattr(pdf, "have_tool", lambda n: n == "exiftool")
        assert pdf._available_tier() == "exiftool"


class TestHtml:
    def test_finds_all(self, html_with_metadata):
        assert kinds(html_md.inspect_html(html_with_metadata)) >= {
            "generator",
            "jsonld",
            "attribute",
            "comment",
        }

    def test_clean_removes_ai_keeps_content(self, html_with_metadata):
        out, _ = html_md.clean_html(html_with_metadata)
        assert b"ChatGPT" not in out
        assert b"AIGeneratedContent" not in out
        assert b"data-ai-model" not in out
        assert b"Copilot" not in out
        assert b"<p>Hello</p>" in out
        assert b'charset="utf-8"' in out

    def test_unrelated_jsonld_survives(self, html_with_metadata):
        out, _ = html_md.clean_html(html_with_metadata)
        assert b'{"@type":"Article"}' in out


class TestMarkdown:
    def test_finds_ai_keys(self, markdown_with_frontmatter):
        details = [
            f.detail for f in html_md.inspect_markdown(markdown_with_frontmatter).findings
        ]
        assert any("ai_generated" in d for d in details)
        assert any("model" in d for d in details)

    def test_clean_keeps_other_keys_and_body(self, markdown_with_frontmatter):
        out, _ = html_md.clean_markdown(markdown_with_frontmatter)
        assert b"ai_generated" not in out
        assert b"gpt-4o" not in out
        assert b"title: Real Title" in out
        assert b"- keep" in out
        assert b"Body paragraph" in out

    def test_no_frontmatter_is_untouched(self):
        data = b"# Just a heading\n\nText.\n"
        out, result = html_md.clean_markdown(data)
        assert out == data
        assert result.findings == []

    def test_frontmatter_removed_entirely_when_all_ai(self):
        data = b"---\nai_generated: true\nmodel: x\n---\n\nBody\n"
        out, _ = html_md.clean_markdown(data)
        assert not out.startswith(b"---")
        assert b"Body" in out


class TestSvg:
    def test_finds_all(self, svg_with_metadata):
        assert kinds(svg.inspect(svg_with_metadata)) >= {"xmp", "attribute", "comment"}

    def test_clean_keeps_graphics(self, svg_with_metadata):
        out, _ = svg.clean(svg_with_metadata)
        assert b"Midjourney" not in out
        assert b"data-ai-model" not in out
        assert b"<metadata>" not in out
        assert b"rect" in out
        assert b'width="10"' in out

    def test_output_is_parseable(self, svg_with_metadata):
        import xml.etree.ElementTree as ET

        out, _ = svg.clean(svg_with_metadata)
        ET.fromstring(out)

    def test_malformed_xml_falls_back_to_regex(self):
        broken = b"<svg><!-- generated by Midjourney --><rect>"
        out, result = svg.clean(broken)
        assert b"Midjourney" not in out
        assert any("parse failed" in w for w in result.warnings)


class TestXmlEntityGuards:
    """Untrusted SVG and OOXML must not be able to detonate the XML parser."""

    BOMB = (
        b'<?xml version="1.0"?><!DOCTYPE svg [\n'
        b'<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
        b'<!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">\n'
        b']><svg xmlns="http://www.w3.org/2000/svg"><text>&d;</text></svg>'
    )
    XXE = b'<?xml version="1.0"?><!DOCTYPE svg SYSTEM "http://attacker.test/x.dtd"><svg/>'

    def test_entity_declaration_refused(self):
        from wmtk.metadata.safexml import UnsafeXml, fromstring

        with pytest.raises(UnsafeXml, match="ENTITY"):
            fromstring(self.BOMB)

    def test_external_doctype_refused(self):
        from wmtk.metadata.safexml import UnsafeXml, fromstring

        with pytest.raises(UnsafeXml, match="external identifier"):
            fromstring(self.XXE)

    def test_bomb_does_not_expand_through_svg(self):
        out, result = svg.clean(self.BOMB)
        assert len(out) <= len(self.BOMB), "entity expansion occurred"
        assert any("ENTITY" in w for w in result.warnings)

    def test_bomb_reported_not_silently_ignored(self):
        assert any("ENTITY" in w for w in svg.inspect(self.BOMB).warnings)

    def test_ordinary_svg_still_parses(self, svg_with_metadata):
        from wmtk.metadata.safexml import fromstring

        assert fromstring(svg_with_metadata) is not None

    def test_guard_does_not_depend_on_expat_internals(self):
        """Regression: an expat-handler guard silently fails open on 3.14+."""
        import xml.etree.ElementTree as ET

        from wmtk.metadata.safexml import scan_for_unsafe

        # Whether or not this interpreter exposes the expat object, the scan
        # must still catch the bomb.
        assert not hasattr(ET.XMLParser(), "parser") or True
        assert scan_for_unsafe(self.BOMB) is not None

    def test_ooxml_entity_bomb_in_props_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("docProps/core.xml", self.BOMB.decode("latin-1"))
            z.writestr("word/document.xml", "<w:document/>")
        result = ooxml.inspect(buf.getvalue())
        assert any("ENTITY" in w for w in result.warnings)
        out, _ = ooxml.clean(buf.getvalue())
        assert len(out) < 10 * len(buf.getvalue())


class TestExiftoolReportFiltering:
    """The exiftool enrichment path only runs where exiftool is installed,
    which was a single CI cell. These tests exercise the filtering everywhere
    by feeding it canned exiftool output."""

    @pytest.mark.parametrize(
        "group,name",
        [
            ("PNG", "ImageWidth"),
            ("PNG", "BitDepth"),
            ("PNG", "ColorType"),
            ("PNG", "Interlace"),
            ("PNG", "Filter"),
            ("File", "FileSize"),
            ("ExifTool", "ExifToolVersion"),
            ("Composite", "Megapixels"),
            ("JFIF", "JFIFVersion"),
            ("RIFF", "ImageWidth"),
            ("QuickTime", "Duration"),
        ],
    )
    def test_structural_properties_are_not_findings(self, group, name):
        """A cleaned PNG must report zero findings, not its own dimensions."""
        from wmtk.metadata import _exiftool_kind

        assert _exiftool_kind(group, name) is None, f"{group}:{name} should be ignored"

    @pytest.mark.parametrize(
        "group,name,kind",
        [
            ("EXIF", "Make", "exif"),
            ("EXIF", "DateTimeOriginal", "exif"),
            ("GPS", "GPSLatitude", "exif"),
            ("XMP", "CreatorTool", "xmp"),
            ("IPTC", "By-line", "iptc"),
            ("JUMBF", "ClaimGenerator", "c2pa"),
            ("C2PA", "Manifest", "c2pa"),
            ("PDF", "Author", "docprops"),
            ("ICC_Profile", "ProfileDescription", "icc"),
            ("Photoshop", "IPTCDigest", "iptc"),
            ("MakerNotes", "SerialNumber", "exif"),
        ],
    )
    def test_provenance_groups_are_reported(self, group, name, kind):
        from wmtk.metadata import _exiftool_kind

        assert _exiftool_kind(group, name) == kind

    @pytest.mark.parametrize(
        "name",
        ["Software", "Comment", "Author", "Copyright", "Parameters", "Prompt"],
    )
    def test_textual_tags_survive_the_allowlist(self, name):
        """PNG:Software is a claim about who made the file, so it is reported
        even though the PNG group itself is structural."""
        from wmtk.metadata import _exiftool_kind

        assert _exiftool_kind("PNG", name) == "text"

    def test_clean_png_reports_nothing_from_exiftool(self, monkeypatch, png_with_metadata):
        """End to end with a stubbed exiftool: a structural-only report must
        produce zero findings, so clean-then-verify actually reads as clean."""
        import json as _json

        from wmtk import metadata as md

        canned = _json.dumps(
            [
                {
                    "SourceFile": "x.png",
                    "ExifTool:ExifToolVersion": 12.76,
                    "File:FileSize": 1234,
                    "PNG:ImageWidth": 1,
                    "PNG:ImageHeight": 1,
                    "PNG:BitDepth": 8,
                    "PNG:ColorType": 2,
                    "Composite:Megapixels": 0.000001,
                }
            ]
        ).encode()

        class Proc:
            stdout = canned
            stderr = b""
            returncode = 0

        monkeypatch.setattr(md, "have_tool", lambda n: n == "exiftool")
        monkeypatch.setattr(md, "run_tool", lambda *a, **k: Proc())

        out, _ = png.clean(png_with_metadata)
        report = md.inspect_file(out, "clean.png", use_tools=True).to_dict()
        assert report["count"] == 0, report["findings"]
        assert report["c2pa_present"] is False
