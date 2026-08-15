"""Input guards, atomic writes, and subprocess argument safety."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path

import pytest

from wmtk import safety
from wmtk.safety import (
    MAGIC_PREFIXES,
    BinaryInputRefused,
    InputTooLarge,
    atomic_write_bytes,
    atomic_write_text,
    check_size,
    detect_binary,
    ensure_text,
    safe_arg_path,
)


class TestSizeCaps:
    def test_under_limit_passes(self):
        check_size(b"x" * 100, limit=1000)

    def test_over_limit_raises(self):
        with pytest.raises(InputTooLarge, match="limit is 10"):
            check_size(b"x" * 11, limit=10)

    def test_measures_encoded_length_for_text(self):
        """A 5-character string of 4-byte codepoints is 20 bytes, not 5."""
        with pytest.raises(InputTooLarge):
            check_size("\U0001f600" * 5, limit=10)


class TestBinaryGuard:
    @pytest.mark.parametrize("magic,label", MAGIC_PREFIXES)
    def test_every_magic_prefix_is_caught(self, magic, label):
        assert detect_binary(magic + b"\x41" * 64) == label

    def test_nul_bytes_caught(self):
        assert detect_binary(b"text\x00more") == "NUL bytes"

    def test_control_density_caught(self):
        assert detect_binary(bytes(range(1, 20)) * 20) == "high control-byte density"

    @pytest.mark.parametrize(
        "data",
        [
            b"plain ascii text",
            b"tabs\tand\nnewlines\r\n",
            "unicode é中\U0001f600".encode(),
            b"",
        ],
    )
    def test_real_text_passes(self, data):
        assert detect_binary(data) is None

    def test_ensure_text_refuses_binary(self):
        with pytest.raises(BinaryInputRefused, match="PDF"):
            ensure_text(b"%PDF-1.7\nstuff")

    def test_force_overrides(self):
        assert ensure_text(b"%PDF-1.7", force=True).startswith("%PDF")

    def test_lone_surrogates_round_trip(self):
        raw = b"caf\xe9 latte"  # latin-1, not valid UTF-8
        text = ensure_text(raw)
        assert text.encode("utf-8", "surrogateescape") == raw


class TestAtomicWrite:
    def test_writes_content(self, tmp_path):
        dest = tmp_path / "out.txt"
        atomic_write_text(dest, "hello")
        assert dest.read_text() == "hello"

    def test_creates_parent_dirs(self, tmp_path):
        dest = tmp_path / "a" / "b" / "out.bin"
        atomic_write_bytes(dest, b"x")
        assert dest.read_bytes() == b"x"

    def test_no_temp_files_left(self, tmp_path):
        atomic_write_bytes(tmp_path / "out.bin", b"x")
        assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]

    def test_refuses_symlink(self, tmp_path):
        target = tmp_path / "real.txt"
        target.write_text("original")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform/account")
        with pytest.raises(PermissionError, match="symlink"):
            atomic_write_bytes(link, b"overwritten")
        assert target.read_text() == "original"

    def test_overwrite_is_atomic_under_concurrency(self, tmp_path):
        """Every reader sees either the old or the new content, never a mix."""
        dest = tmp_path / "race.bin"
        old, new = b"A" * 4096, b"B" * 4096
        dest.write_bytes(old)
        seen: list[bytes] = []
        stop = threading.Event()

        def reader() -> None:
            # A real reader does not spin at 100%. A zero-delay loop can starve
            # the Windows rename indefinitely, which tests the scheduler rather
            # than the atomicity property this case is about.
            while not stop.is_set():
                with contextlib.suppress(OSError):
                    seen.append(dest.read_bytes())
                time.sleep(0.0005)

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        for _ in range(30):
            atomic_write_bytes(dest, new)
            atomic_write_bytes(dest, old)
        stop.set()
        t.join(timeout=5)
        assert set(seen) <= {old, new}, "a partial write was observed"

    def test_write_failure_leaves_original(self, tmp_path, monkeypatch):
        dest = tmp_path / "keep.bin"
        dest.write_bytes(b"original")

        real_replace = os.replace

        def boom(src, dst):
            raise OSError("simulated failure")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(OSError):
            atomic_write_bytes(dest, b"new")
        monkeypatch.setattr(os, "replace", real_replace)
        assert dest.read_bytes() == b"original"
        assert list(tmp_path.iterdir()) == [dest], "temp file was left behind"


class TestSubprocessArgs:
    @pytest.mark.parametrize(
        "name",
        ["-delete", "--all=", "-overwrite_original", "-rf"],
    )
    def test_leading_dash_is_neutralized(self, name):
        """A file called -delete must not reach exiftool as a flag."""
        out = safe_arg_path(name)
        assert not out.startswith("-")
        assert out.endswith(name)

    def test_ordinary_paths_untouched(self):
        assert safe_arg_path("photo.jpg") == "photo.jpg"

    def test_missing_tool_raises_toolmissing(self):
        with pytest.raises(safety.ToolMissing):
            safety.run_tool(["definitely-not-a-real-binary-xyz"])

    def test_have_tool_matches_which(self):
        assert safety.have_tool("definitely-not-a-real-binary-xyz") is False


class TestTempWorkdir:
    def test_cleans_itself_up(self):
        with safety.temp_workdir() as tmp:
            path = Path(tmp)
            (path / "scratch").write_bytes(b"x")
            assert path.is_dir()
        assert not path.exists()
