"""Layer A: injected invisibles are found and removed; real ones survive."""

from __future__ import annotations

import pytest

from scrubai.layer_a import clean, diff_ops, scan
from scrubai.unicode_tables import classify, is_load_bearing
from tests.conftest import INJECTED


class TestScan:
    def test_clean_text_is_clean(self, clean_text):
        result = scan(clean_text)
        assert result.findings == []
        assert not result.to_dict()["watermark_suspected"]

    def test_finds_every_injected_codepoint(self, dirty_text):
        result = scan(dirty_text)
        found = {f.codepoint: f.count for f in result.findings}
        for ch, expected in INJECTED.items():
            cp = ord(ch)
            assert cp in found, f"missed U+{cp:04X}"
            assert found[cp] == expected, f"U+{cp:04X} counted {found[cp]}, want {expected}"

    def test_counts_match_clean_pass(self, dirty_text):
        """A scan's removable count must equal what a clean actually does."""
        scanned = scan(dirty_text)
        cleaned = clean(dirty_text)
        assert scanned.total_removable == (cleaned.total_removed + cleaned.total_replaced)

    def test_offsets_are_real(self, dirty_text):
        for f in scan(dirty_text).findings:
            for off in f.offsets:
                assert ord(dirty_text[off]) == f.codepoint

    def test_offsets_capped_at_ten(self):
        text = "a​" * 50
        f = scan(text).findings[0]
        assert len(f.to_dict()["offsets"]) == 10
        assert f.to_dict()["offsets_truncated"] is True

    def test_confusables_need_aggressive(self):
        text = "Bank of Amеrica"  # Cyrillic small ie
        assert scan(text).findings == []
        assert scan(text, aggressive=True).findings[0].kind == "confusable"


class TestClean:
    def test_removes_everything_flagged(self, dirty_text, clean_text):
        result = clean(dirty_text)
        assert result.text == clean_text
        assert scan(result.text).findings == []

    def test_idempotent(self, dirty_text):
        once = clean(dirty_text).text
        assert clean(once).text == once

    def test_nbsp_becomes_space_not_deleted(self):
        result = clean("a b")
        assert result.text == "a b"
        assert result.total_replaced == 1
        assert result.total_removed == 0

    def test_normalize_is_opt_in(self):
        text = "ﬁve"  # ligature fi
        assert clean(text).text == text
        assert clean(text, normalize=True).text == "five"

    def test_stats_label_each_codepoint(self, dirty_text):
        removed = clean(dirty_text).removed
        assert any("ZERO WIDTH SPACE" in k for k in removed)
        assert all(k.startswith("U+") for k in removed)


class TestLoadBearing:
    def test_preserved_by_default(self, load_bearing_text):
        result = clean(load_bearing_text)
        assert result.text == load_bearing_text
        assert result.total_removed == 0
        assert result.kept

    @pytest.mark.parametrize(
        "text,index,reason",
        [
            ("\U0001f469‍\U0001f467", 1, "emoji_sequence"),
            ("پ‌ر", 1, "script_joiner"),
            ("❤️", 1, "emoji_sequence"),
            ("\U0001f3f4\U000e0067", 1, "flag_tag_sequence"),
            ("ᠠ᠋", 1, "mongolian_fvs"),
            ("ក឴", 1, "khmer_inherent_vowel"),
        ],
    )
    def test_each_reason(self, text, index, reason):
        assert is_load_bearing(text, index) == reason

    @pytest.mark.parametrize(
        "text,index",
        [
            ("a‍", 1),  # ZWJ after ASCII: not a script joiner
            ("​", 0),  # ZWSP at position 0
            ("hello️", 5),  # VS16 after a plain letter
        ],
    )
    def test_not_load_bearing(self, text, index):
        assert is_load_bearing(text, index) is None

    def test_tag_run_not_anchored_to_emoji_is_stripped(self):
        """Regression: a smuggled tag run must not shield itself.

        Each tag character's predecessor is another tag character, so a naive
        check sees every tag after the first as part of a flag sequence and
        keeps the whole hidden payload.
        """
        smuggled = "Hello" + "".join(chr(0xE0000 + ord(c)) for c in "SECRET")
        result = clean(smuggled)
        assert result.text == "Hello"
        assert result.total_removed == 6
        assert result.kept == {}

    def test_real_flag_tag_sequence_survives(self):
        flag = "\U0001f3f4" + "".join(chr(0xE0000 + ord(c)) for c in "gbsct") + "\U000e007f"
        assert clean(flag).text == flag

    def test_strip_emoji_glue_overrides(self, load_bearing_text):
        result = clean(load_bearing_text, strip_emoji_glue=True)
        assert result.total_removed > 0
        assert result.text != load_bearing_text

    def test_scan_reports_kept_separately(self, load_bearing_text):
        result = scan(load_bearing_text)
        assert result.total_flagged > 0
        assert result.total_removable == 0
        assert not result.to_dict()["watermark_suspected"]


class TestClassification:
    @pytest.mark.parametrize(
        "cp,kind",
        [
            (0x200B, "zwj_family"),
            (0x200E, "bidi"),
            (0x202E, "bidi"),
            (0xE0041, "tag_chars"),
            (0xFE0F, "variation_selector"),
            (0xE0100, "variation_selector"),
            (0xE000, "private_use"),
            (0x100001, "private_use"),
            (0x00A0, "space"),
            (0x3000, "space"),
            (0x2060, "other_cf"),
            (0x00AD, "strip"),
            (0xFEFF, "strip"),
        ],
    )
    def test_kinds(self, cp, kind):
        cls = classify(cp)
        assert cls is not None and cls.kind == kind

    @pytest.mark.parametrize("cp", [0x0600, 0x06DD, 0x070F, 0x110BD])
    def test_orthographic_marks_never_flagged(self, cp):
        assert classify(cp) is None

    @pytest.mark.parametrize("cp", [ord("a"), ord(" "), 0x00E9, 0x4E2D, 0x1F600])
    def test_ordinary_characters_pass(self, cp):
        assert classify(cp) is None


class TestDiff:
    def test_marks_deletion_and_replacement(self):
        ops = diff_ops("a​b c", "ab c")
        kinds = {o["op"] for o in ops}
        assert "delete" in kinds
        assert any(o["op"] == "replace" or o["op"] == "delete" for o in ops)

    def test_identical_input_is_all_equal(self, clean_text):
        ops = diff_ops(clean_text, clean_text)
        assert [o["op"] for o in ops] == ["equal"]
