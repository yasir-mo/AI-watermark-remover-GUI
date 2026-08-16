"""Input guards, atomic writes, and safe subprocess invocation.

Every entry point (CLI, HTTP, library) funnels through these helpers so the
limits are applied in exactly one place.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

MAX_INPUT_BYTES = int(os.environ.get("WATERMARKS_MAX_INPUT_BYTES", 256 * 1024 * 1024))
MAX_TEXT_BYTES = int(os.environ.get("WATERMARKS_MAX_TEXT_BYTES", 32 * 1024 * 1024))
SUBPROCESS_TIMEOUT = int(os.environ.get("WATERMARKS_SUBPROCESS_TIMEOUT", 120))

RLIMIT_AS_BYTES = 4 * 1024 * 1024 * 1024
RLIMIT_FSIZE_BYTES = 2 * 1024 * 1024 * 1024


class InputTooLarge(ValueError):
    """Payload exceeded the configured cap."""


class BinaryInputRefused(ValueError):
    """Data looks like a binary/compressed blob but was handed to a text path."""


class ToolMissing(RuntimeError):
    """An optional external tool was required but is not installed."""


class ToolFailed(RuntimeError):
    """An external tool ran and returned non-zero."""


def check_size(data: bytes | str, *, limit: int = MAX_INPUT_BYTES) -> None:
    n = len(data.encode("utf-8", "surrogatepass")) if isinstance(data, str) else len(data)
    if n > limit:
        raise InputTooLarge(f"input is {n} bytes, limit is {limit}")


# --------------------------------------------------------------------------
# Binary-as-text guard
# --------------------------------------------------------------------------

# Magic-byte prefixes for formats that must never be treated as plain text.
MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "ZIP"),
    (b"PK\x05\x06", "ZIP"),
    (b"PK\x07\x08", "ZIP"),
    (b"%PDF-", "PDF"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"II*\x00", "TIFF"),
    (b"MM\x00*", "TIFF"),
    (b"RIFF", "RIFF"),
    (b"OggS", "Ogg"),
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "RAR"),
    (b"\x7fELF", "ELF"),
    (b"\xcf\xfa\xed\xfe", "Mach-O"),
    (b"\xce\xfa\xed\xfe", "Mach-O"),
    (b"\xca\xfe\xba\xbe", "Mach-O fat"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "legacy Office"),
    (b"SQLite format 3\x00", "SQLite"),
    (b"8BPS", "PSD"),
    (b"wOFF", "WOFF"),
    (b"wOF2", "WOFF2"),
    (b"\x00\x01\x00\x00\x00", "TrueType"),
    (b"OTTO", "OpenType"),
    (b"MZ", "PE executable"),
)

_TEXT_CONTROL_ALLOWED = {0x09, 0x0A, 0x0D, 0x0C, 0x1B}


def detect_binary(data: bytes) -> str | None:
    """Return a format label if ``data`` looks binary, else None."""
    for magic, label in MAGIC_PREFIXES:
        if data.startswith(magic):
            return label

    if b"\x00" in data[:8192]:
        return "NUL bytes"

    sample = data[:8192]
    if not sample:
        return None
    suspicious = sum(1 for b in sample if b < 0x20 and b not in _TEXT_CONTROL_ALLOWED)
    if suspicious / len(sample) > 0.05:
        return "high control-byte density"
    return None


def ensure_text(data: bytes, *, force: bool = False) -> str:
    """Decode ``data`` as UTF-8 text, refusing obvious binary unless forced."""
    check_size(data, limit=MAX_TEXT_BYTES)
    if not force:
        label = detect_binary(data)
        if label is not None:
            raise BinaryInputRefused(
                f"input looks like binary data ({label}); pass --force-text to override"
            )
    return data.decode("utf-8", errors="surrogateescape")


# --------------------------------------------------------------------------
# Atomic writes
# --------------------------------------------------------------------------


REPLACE_DEADLINE = float(os.environ.get("WATERMARKS_REPLACE_DEADLINE", "5.0"))
REPLACE_BACKOFF_MAX = 0.05


def _replace_with_retry(tmp: str, dest: Path) -> None:
    """os.replace, retrying on Windows sharing violations until a deadline.

    On POSIX, rename(2) over an open file always succeeds. On Windows a reader
    holding the destination open makes MoveFileEx fail with
    ERROR_ACCESS_DENIED or ERROR_SHARING_VIOLATION, so a concurrent reader
    would turn an ordinary write into a hard error.

    Retrying does not weaken atomicity: the swap still either happens whole or
    not at all, and no reader ever observes a partial file. The bound is a
    wall-clock deadline instead of an attempt count, so a slow machine behaves
    the same as a fast one.
    """
    deadline = time.monotonic() + REPLACE_DEADLINE
    backoff = 0.005
    while True:
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(backoff)
            backoff = min(backoff * 1.6, REPLACE_BACKOFF_MAX)


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Write ``data`` to ``path`` atomically, refusing to follow symlinks."""
    dest = Path(path)
    if dest.is_symlink():
        raise PermissionError(f"refusing to write through symlink: {dest}")
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".wmtk-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, dest)
        tmp = ""
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8", "surrogateescape"))


# --------------------------------------------------------------------------
# Safe subprocess
# --------------------------------------------------------------------------


def safe_arg_path(path: str | os.PathLike[str]) -> str:
    """Prefix a path with ``./`` when it starts with ``-``.

    Prevents a filename like ``-delete`` from being parsed as a flag by
    exiftool / qpdf / c2patool.
    """
    s = os.fspath(path)
    if s.startswith("-"):
        return os.path.join(".", s)
    return s


def _preexec_limits() -> None:  # pragma: no cover - POSIX only
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))


def have_tool(name: str) -> bool:
    return shutil.which(name) is not None


def run_tool(
    argv: Sequence[str],
    *,
    timeout: int = SUBPROCESS_TIMEOUT,
    check: bool = True,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run an external tool with resource limits and no shell."""
    exe = shutil.which(argv[0])
    if exe is None:
        raise ToolMissing(f"{argv[0]} is not installed")

    kwargs: dict[str, object] = {}
    if os.name == "posix":
        kwargs["preexec_fn"] = _preexec_limits

    proc = subprocess.run(  # noqa: S603 - argv is a list, shell=False
        [exe, *argv[1:]],
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        raise ToolFailed(f"{argv[0]} exited {proc.returncode}: {stderr[:500]}")
    return proc


def temp_workdir() -> tempfile.TemporaryDirectory[str]:
    """A private 0700 temp directory that cleans itself up."""
    return tempfile.TemporaryDirectory(prefix="wmtk-")
