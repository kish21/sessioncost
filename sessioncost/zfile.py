"""Open a log that may be zstd-compressed (Codex packs older threads as rollout-*.jsonl.zst).

No hard dependency: Python 3.14+ has `compression.zstd` built in; on older Python the optional `zstandard` package
(BSD-3-Clause) is used when installed. Without either, compressed files are skipped and `zstd_note` says so.
"""
from __future__ import annotations

import functools
import io
import sys
from pathlib import Path
from typing import IO


@functools.lru_cache(maxsize=1)                     # a failed import is slow; looked up once per run
def _module():
    try:
        from compression import zstd   # Python 3.14+
        return zstd
    except ImportError:
        pass
    try:
        import zstandard               # optional: pip install zstandard
        return zstandard
    except ImportError:
        return None


def can_unpack() -> bool:
    return _module() is not None


def is_zst(path: Path) -> bool:
    return path.suffix == ".zst"


def open_text(path: Path) -> IO[str]:
    """The file as text, unpacked if it ends in .zst. Raises OSError when it cannot be unpacked."""
    if not is_zst(path):
        return path.open(encoding="utf-8", errors="replace")
    m = _module()
    if m is None:
        raise OSError(f"{path.name} is compressed: {zstd_hint()}")
    try:
        if m.__name__ == "compression.zstd":
            stream = m.open(path, "rb")
        else:
            stream = m.ZstdDecompressor().stream_reader(path.open("rb"), closefd=True, read_across_frames=True)
    except Exception as e:             # a broken file must not stop the others
        raise OSError(f"{path.name}: {e}") from e
    return io.TextIOWrapper(stream, encoding="utf-8", errors="replace")


def zstd_hint() -> str:
    # the Python that runs SessionCost (installer and npm keep a private one): a bare "pip" may be another Python
    return f'to read them, run "{sys.executable}" -m pip install zstandard (free), or use Python 3.14 or newer'


def codex_note(before: str = "", after: str = "") -> str:
    """The `zstd_note` for this machine's Codex folder, wrapped in `before`/`after`; "" when nothing is left out."""
    from .locate import codex_unreadable
    note = zstd_note(codex_unreadable())
    return f"{before}{note}{after}" if note else ""


def zstd_note(skipped: int) -> str:
    return (f"{skipped} older Codex chat{'s are' if skipped != 1 else ' is'} compressed and not shown: "
            f"{zstd_hint()}.") if skipped else ""
